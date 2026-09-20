"""A real OCCT elevation stays bound to its committed Stage after reopening."""

from __future__ import annotations

from pathlib import Path
import base64
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from archflow.adapters import occt_backend
from archflow.project.refs import record_ref_from_uri
from monkeydiagram.drawing_elevation import read_model_axis_elevation
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.gestures import require_document_comment_source
from archflow_studio_api.application.projection import project_state

from .support import PROJECT_ID, retain_runner_receipt
from .test_candidate import CandidateTestCase
from .test_documents import image_bytes
from .test_working_copies import register_model


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class DrawingTests(CandidateTestCase):
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
        model = next(row for row in candidate["artifacts"] if row["format"] == "3dm")
        self.model = model["modelSource"]
        self.step = next(row for row in candidate["artifacts"] if row["format"] == "step")
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": self.model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        self.stage = initialized.json()
        self.head = self.repository.read_head()

    def generate(self, stage: dict | None = None, **body: object):
        return self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceStageRef": (stage or self.stage)["stageRef"],
            "view": "front", "drawingId": "main-elevation", **body,
        })

    def sheet(self, **body):
        return self.client.post("/api/drawings/sheets", json={
            "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"],
            "styleId": "arch400-white", **body,
        })

    def test_model_view_projects_the_exact_step_without_writing_a_drawing(self):
        from monkeydiagram.drawing_elevation import project_model_axis_elevation

        project_root = self.repository.layout.root
        before = {path.relative_to(project_root): path.read_bytes() for path in project_root.rglob("*") if path.is_file()}
        for view in ("front", "back", "left", "right", "top"):
            with self.subTest(view=view), patch(
                "archflow_studio_api.application.drawings.project_model_axis_elevation",
                wraps=project_model_axis_elevation,
            ) as project:
                response = self.client.get("/api/drawings/model-view", params={**self.model, "view": view})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual((result["source"], result["view"]), (self.model, view))
                self.assertEqual((result["mimeType"], result["representation"]), ("image/png", "orthographic-line-projection"))
                data = base64.b64decode(result["data"], validate=True)
                with Image.open(BytesIO(data)) as image:
                    image.load()
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (result["width"], result["height"]))
                    self.assertLessEqual(max(image.size), 1024)
                    self.assertLess(image.convert("L").getextrema()[0], 255, "the real model must leave visible lines")
                self.assertEqual(project.call_count, 1, "one observation projects once")
        after = {path.relative_to(project_root): path.read_bytes() for path in project_root.rglob("*") if path.is_file()}
        self.assertEqual(after, before, "observation creates no project files or records and does not change HEAD")

    def test_model_view_keeps_the_named_run_after_a_new_candidate_and_bounds_large_models(self):
        original = self.client.get("/api/drawings/model-view", params=self.model)
        self.assertEqual(original.status_code, 200, original.text)
        accepted, job = self.run_candidate("set height to 2000", elementId="portico-base",
                                           stateDigest=self.model["stateDigest"], sourceRunId=self.model["runId"])
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        newer = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        self.assertNotEqual(newer["stateDigest"], self.model["stateDigest"])
        response = self.client.get("/api/drawings/model-view", params=newer)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["source"], newer)
        self.assertLessEqual(max(result["width"], result["height"]), 1024)
        self.assertNotEqual(result["data"], original.json()["data"])
        with TestClient(create_app(self.settings)) as reopened:
            retained = reopened.get("/api/drawings/model-view", params=self.model)
            self.assertEqual(retained.status_code, 200, retained.text)
            self.assertEqual(retained.json(), original.json(), "cold observation follows the exact requested run")
            mixed = reopened.get("/api/drawings/model-view", params={**self.model, "runId": newer["runId"]})
            self.assertEqual(mixed.status_code, 409, mixed.text)
            self.assertEqual(mixed.json()["code"], "MODEL_SOURCE_MISMATCH")

    def test_model_view_refuses_mismatched_or_tampered_sources_before_projection(self):
        with patch("archflow_studio_api.application.drawings.project_model_axis_elevation",
                   side_effect=AssertionError("invalid sources must not be rendered")):
            for key in ("stateDigest", "assetSha256"):
                response = self.client.get("/api/drawings/model-view", params={**self.model, key: "0" * 64})
                self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.client.get("/api/drawings/model-view", params={**self.model, "view": "perspective"}).status_code, 422)
            step_path = self.repository.layout.root / self.step["relativePath"]
            step_path.write_bytes(step_path.read_bytes() + b"\nchanged-source")
            response = self.client.get("/api/drawings/model-view", params=self.model)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_sheet_styles_generate_scaled_pdfs_on_source_run_and_reuse_after_restart(self):
        from pypdf import PdfReader
        from archflow.adapters.cad_execution import project_occt_lines

        self.enable_monitor()
        styles = self.client.get("/api/drawings/styles")
        self.assertEqual(styles.status_code, 200, styles.text)
        catalog = {style["id"]: style for style in styles.json()["styles"]}
        self.assertEqual(set(catalog), {"arch400-white", "arch364-technical"})
        before_runs = set(self.repository.layout.runs.iterdir())
        for style_id, scale in (("arch400-white", 20), ("arch364-technical", 40)):
            with self.subTest(style=style_id), patch(
                "archflow_studio_api.application.drawings.project_occt_lines", wraps=project_occt_lines,
            ) as project:
                result = self.sheet(styleId=style_id, scaleDenominator=scale, notes=["Review dimensions on the retained model."])
                self.assertEqual(result.status_code, 201, result.text)
                document = result.json()
                self.assertEqual(project.call_count, 3)
                self.assertEqual([call.kwargs["right"] for call in project.call_args_list],
                                 [(1, 0, 0), (0, 1, 0), (1, 0, 0)])
                self.assertEqual([call.kwargs["up"] for call in project.call_args_list],
                                 [(0, 0, 1), (0, 0, 1), (0, 1, 0)])
                self.assertIsNone(document["revisionRef"])
                self.assertEqual((document["runId"], document["modelSource"], document["sourceStageRef"]),
                                 (self.model["runId"], self.model, self.stage["stageRef"]))
                self.assertEqual(document["viewRecipe"]["style"]["id"], style_id)
                self.assertEqual(document["viewRecipe"]["style"]["version"], "1")
                self.assertEqual(document["viewRecipe"]["scaleDenominator"], scale)
                events = self.drawing_events()
                parent = next(event for event in events if event.phase == "drawing_generate"
                              and event.status == "succeeded"
                              and event.details["input_identity"]["view_recipe"]["style_id"] == style_id)
                self.assertEqual(parent.details["cache_status"], "miss")
                projections = [event for event in events if event.parent_event_id == parent.event_id
                               and event.phase == "drawing.hlr" and event.status == "succeeded"]
                self.assertEqual({event.details["input_identity"]["view_recipe"]["view"] for event in projections},
                                 {"front", "right", "top"})
                data = self.client.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": document["runId"]})
                self.assertEqual(data.status_code, 200, data.text[:100] if data.status_code != 200 else "")
                pdf = PdfReader(BytesIO(data.content))
                self.assertEqual(len(pdf.pages), 1)
                for actual, mm in zip((pdf.pages[0].mediabox.width, pdf.pages[0].mediabox.height), catalog[style_id]["paperSizeMm"]):
                    self.assertAlmostEqual(float(actual) * 25.4 / 72, mm, places=3)
                self.assertEqual(json.loads(pdf.metadata["/ArchFlowViewRecipe"]), document["viewRecipe"])
                workspace = self.repository.layout.run(document["runId"]).workspaces / "documentation" / document["assetSha256"] / "sheet.pdf"
                self.assertEqual(workspace.read_bytes(), data.content)
                import ezdxf

                dxf = ezdxf.readfile(workspace.with_suffix(".dxf"))
                self.assertTrue(any(len(layout) for layout in dxf.layouts if layout.name != "Model"))
                with TestClient(create_app(self.settings)) as reopened, patch(
                    "archflow_studio_api.application.drawings.project_occt_lines", side_effect=AssertionError("cached sheet cannot run HLR"),
                ), patch("monkeydiagram.drawing_output.render_pdf", side_effect=AssertionError("cached PDF cannot be rerendered")):
                    repeated = reopened.post("/api/drawings/sheets", json={
                        "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "styleId": style_id,
                        "scaleDenominator": scale, "notes": ["Review dimensions on the retained model."],
                    })
                    self.assertEqual(repeated.status_code, 201, repeated.text)
                    self.assertEqual(repeated.json(), document)
                    self.assertEqual(reopened.get(f"/api/documents/{document['assetSha256']}/bytes",
                                                 params={"runId": document["runId"]}).content, data.content)
        documents = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertEqual([row["drawingId"] for row in documents[:2]], ["arch364-technical", "arch400-white"])
        self.assertEqual(set(self.repository.layout.runs.iterdir()), before_runs)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_sheet_hidden_objects_are_removed_before_visibility_and_recipe_changes_keep_old_pdf(self):
        from archflow.adapters.cad_execution import project_occt_lines

        receipt = self.repository.load_json(record_ref_from_uri(self.step["receiptRef"], PROJECT_ID))
        physical = set(receipt["physical_object_ids"])
        hidden = next(name for name in physical if "base" in name)
        outline = next(name for name in physical if "cornice" in name)
        original = self.sheet()
        self.assertEqual(original.status_code, 201, original.text)
        with patch("archflow_studio_api.application.drawings.project_occt_lines", wraps=project_occt_lines) as project:
            result = self.sheet(hiddenObjectIds=[hidden], outlineObjectIds=[outline], notes=["Base hidden for review."])
        self.assertEqual(result.status_code, 201, result.text)
        changed = result.json()
        self.assertEqual(project.call_count, 3)
        for call in project.call_args_list:
            self.assertEqual(set(call.kwargs["object_ids"]), physical - {hidden})
        self.assertEqual(changed["viewRecipe"]["hiddenObjectIds"], [hidden])
        self.assertEqual(changed["viewRecipe"]["outlineObjectIds"], [outline])
        self.assertNotEqual(changed["assetSha256"], original.json()["assetSha256"])
        documents = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertEqual([row["assetSha256"] for row in documents[:2]], [changed["assetSha256"], original.json()["assetSha256"]])

    def test_sheet_invalid_selections_or_oversized_scale_leave_no_drawing(self):
        receipt = self.repository.load_json(record_ref_from_uri(self.step["receiptRef"], PROJECT_ID))
        physical = receipt["physical_object_ids"]
        with patch("archflow_studio_api.application.drawings.project_occt_lines", side_effect=AssertionError("invalid request cannot run HLR")):
            for body, code in (
                ({"hiddenObjectIds": ["not-a-physical-object"]}, "DRAWING_OBJECT_UNKNOWN"),
                ({"outlineObjectIds": ["not-a-physical-object"]}, "DRAWING_OBJECT_UNKNOWN"),
                ({"hiddenObjectIds": physical}, "DRAWING_EMPTY"),
                ({"hiddenObjectIds": [physical[0]], "outlineObjectIds": [physical[0]]}, "DRAWING_OBJECT_CONFLICT"),
                ({"scaleDenominator": 1}, "DRAWING_GENERATION_FAILED"),
            ):
                with self.subTest(body=body):
                    response = self.sheet(**body)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["code"], code)
        mismatch = self.sheet(modelSource={**self.model, "stateDigest": "0" * 64})
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(mismatch.json()["code"], "DRAWING_SOURCE_MISMATCH")
        self.assertEqual(self.sheet(projectId="other-project").status_code, 403)
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])
        self.assertFalse((self.repository.layout.run(self.model["runId"]).workspaces / "documentation").exists())
        self.assertEqual(self.repository.read_head(), self.head)

    def test_sheet_identical_geometry_keeps_distinct_stage_recipes_and_rejects_changed_step(self):
        from pypdf import PdfReader

        staged = self.sheet()
        self.assertEqual(staged.status_code, 201, staged.text)
        direct = self.sheet(sourceStageRef=None, modelSource=self.model)
        self.assertEqual(direct.status_code, 201, direct.text)
        self.assertNotEqual(staged.json()["assetSha256"], direct.json()["assetSha256"])
        self.assertIsNone(direct.json()["sourceStageRef"])
        pages = []
        for document in (staged.json(), direct.json()):
            data = self.client.get(f"/api/documents/{document['assetSha256']}/bytes", params={"runId": document["runId"]}).content
            pages.append(PdfReader(BytesIO(data)).pages[0].get_contents().get_data())
        self.assertEqual(*pages)
        step_path = self.repository.layout.root / self.step["relativePath"]
        step_path.write_bytes(step_path.read_bytes() + b"\nchanged-source")
        with patch("archflow_studio_api.application.drawings.project_occt_lines", side_effect=AssertionError("changed source cannot run HLR")):
            response = self.sheet()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.repository.read_head(), self.head)

    def enable_monitor(self):
        self.client.close()
        self.settings = replace(self.settings, monitor_dir=self.root / "diagnostics")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def drawing_events(self):
        events, warnings = self.app.state.monitor.store.read()
        self.assertFalse(warnings)
        return [event for event in events if event.phase == "drawing_generate" or event.phase.startswith("drawing.")]

    def test_top_projection_keeps_plan_dimensions_stage_source_and_cold_cache(self) -> None:
        self.enable_monitor()
        front = self.generate()
        self.assertEqual(front.status_code, 201, front.text)
        response = self.generate(view="top")
        self.assertEqual(response.status_code, 201, response.text)
        top = response.json()
        recipe = top["viewRecipe"]
        self.assertEqual((recipe["look"], recipe["right"], recipe["up"]), ([0, 0, -1], [1, 0, 0], [0, 1, 0]))
        self.assertEqual(recipe["name"], "elevation-top")
        self.assertNotEqual(top["revisionRef"], front.json()["revisionRef"])
        self.assertEqual((top["modelSource"], top["sourceStageRef"]), (self.model, self.stage["stageRef"]))
        receipt = self.repository.load_json(record_ref_from_uri(self.step["receiptRef"], PROJECT_ID))
        bounds = [receipt["readback"][name]["bbox"] for name in receipt["physical_object_ids"]]
        left, right = min(b["min"][0] for b in bounds), max(b["max"][0] for b in bounds)
        bottom, upper = min(b["min"][1] for b in bounds), max(b["max"][1] for b in bounds)
        margin = max(right - left, upper - bottom, 0.001) * 0.05
        for actual, expected in zip(recipe["crop_uv"], (left - margin, bottom - margin, right + margin, upper + margin)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(recipe["near_depth"], -max(b["max"][2] for b in bounds) - margin)
        self.assertAlmostEqual(recipe["far_depth"], -min(b["min"][2] for b in bounds) + margin)
        drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(top["revisionRef"], PROJECT_ID))
        self.assertEqual(drawing.receipt["source"]["step"]["sha256"], self.step["sha256"])
        self.assertEqual(drawing.run.base, self.head)
        with TestClient(create_app(self.settings)) as reopened, patch(
            "archflow_studio_api.application.drawings.freeze_model_axis_elevation",
            side_effect=AssertionError("the retained top projection must not be regenerated"),
        ):
            repeated = reopened.post("/api/drawings/elevations", json={
                "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "view": "top", "drawingId": "main-elevation",
            })
            self.assertEqual(repeated.status_code, 201, repeated.text)
            self.assertEqual(repeated.json(), top)
            data = reopened.get(f"/api/documents/{top['assetSha256']}/bytes", params={"runId": top["runId"], "revisionRef": top["revisionRef"]})
            self.assertEqual((data.status_code, data.content), (200, drawing.png))
        parents = [event for event in self.drawing_events() if event.phase == "drawing_generate"]
        self.assertEqual([event.details["cache_status"] for event in parents], ["miss", "miss", "hit"])
        self.assertEqual(parents[1].details["cache_checks"]["view_recipe"], "changed")
        self.assertEqual(self.repository.read_head(), self.head)

    def test_timing_records_real_projection_then_a_cold_cache_hit_without_regenerating(self) -> None:
        self.enable_monitor()
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        events = self.drawing_events()
        parent = next(event for event in events if event.phase == "drawing_generate")
        children = [event for event in events if event.parent_event_id == parent.event_id]
        self.assertEqual([event.phase for event in children],
                         ["drawing.load", "drawing.hlr", "drawing.svg", "drawing.png", "drawing.persist", "drawing.register"])
        self.assertEqual(parent.details["executed_stages"], [event.phase for event in children])
        self.assertEqual(parent.details["cache_status"], "miss")
        self.assertEqual(parent.details["cache_reason"], "no_registered_drawing")
        self.assertEqual(parent.details["execution_path"], "full_projection")
        self.assertEqual(parent.details["input_identity"]["step_sha256"], self.step["sha256"])
        self.assertIn("backend_version", parent.details["input_identity"])
        self.assertEqual(parent.details["scope"], "global_visibility")
        self.assertIn(first.json()["revisionRef"], parent.details["output_refs"])
        self.assertEqual(parent.source_ref, self.stage["stageRef"])
        self.assertEqual(parent.run_id, self.model["runId"])
        for event in (parent, *children):
            self.assertEqual(event.operation_id, parent.operation_id)
            self.assertEqual(event.status, "succeeded")
            self.assertEqual(event.timing_scope, "service")
            self.assertFalse(event.model_call)
            self.assertTrue(all(value is None for value in event.tokens.to_dict().values()))
            self.assertGreaterEqual(event.duration_ms, 0)
            self.assertGreaterEqual(datetime.fromisoformat(event.started_at), datetime.fromisoformat(parent.started_at))
            self.assertLessEqual(datetime.fromisoformat(event.ended_at), datetime.fromisoformat(parent.ended_at))
        hlr = next(event for event in children if event.phase == "drawing.hlr")
        self.assertEqual(hlr.details["input_object_ids"], parent.details["input_object_ids"])
        self.assertNotIn("recomputed_object_ids", hlr.details)
        with TestClient(create_app(self.settings)) as reopened, patch(
            "archflow_studio_api.application.drawings.freeze_model_axis_elevation",
            side_effect=AssertionError("a verified cache hit must not project again"),
        ):
            second = reopened.post("/api/drawings/elevations", json={
                "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"],
                "view": "front", "drawingId": "main-elevation",
            })
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json(), first.json())
        events = self.drawing_events()
        reused = next(event for event in events if event.phase == "drawing_generate" and event.event_id != parent.event_id)
        self.assertEqual(reused.details["cache_status"], "hit")
        self.assertEqual(reused.details["cache_reason"], "exact_registered_drawing")
        self.assertEqual(reused.details["cache_checks"]["bytes"], "same")
        self.assertEqual(reused.details["comparison_refs"], [first.json()["revisionRef"]])
        self.assertEqual(reused.details["input_identity"], parent.details["input_identity"])
        self.assertEqual(reused.details["executed_stages"], [])
        self.assertFalse(any(event.parent_event_id == reused.event_id for event in events))

    def test_changed_view_records_the_compared_revision_and_actual_cache_difference(self) -> None:
        self.enable_monitor()
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        changed = self.generate(view="right")
        self.assertEqual(changed.status_code, 201, changed.text)
        parents = [event for event in self.drawing_events() if event.phase == "drawing_generate"]
        self.assertEqual(len(parents), 2)
        second = parents[-1]
        self.assertEqual(second.details["cache_status"], "miss")
        self.assertEqual(second.details["cache_reason"], "registered_inputs_changed")
        self.assertEqual(second.details["cache_checks"], {
            "drawing_id": "same", "source_stage_ref": "same", "model_source": "same", "view_recipe": "changed",
        })
        self.assertEqual(second.details["comparison_refs"], [first.json()["revisionRef"]])
        self.assertNotEqual(second.details["input_identity"], parents[0].details["input_identity"])
        self.assertIn("drawing.hlr", second.details["executed_stages"])

    def test_binding_only_miss_exposes_equal_inputs_and_two_actual_projection_calls(self) -> None:
        self.enable_monitor()
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "modelSource": self.model, "view": "front", "drawingId": "main-elevation",
        })
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["assetSha256"], first.json()["assetSha256"])
        self.assertNotEqual(second.json()["revisionRef"], first.json()["revisionRef"])
        events = self.drawing_events()
        parents = [event for event in events if event.phase == "drawing_generate"]
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[1].details["input_identity"], parents[0].details["input_identity"])
        self.assertEqual(parents[1].details["cache_status"], "miss")
        self.assertEqual(parents[1].details["cache_checks"], {
            "drawing_id": "same", "source_stage_ref": "changed", "model_source": "same", "view_recipe": "same",
        })
        self.assertEqual(parents[1].details["comparison_refs"], [first.json()["revisionRef"]])
        self.assertEqual(len([event for event in events if event.phase == "drawing.hlr" and event.status == "succeeded"]), 2)
        self.assertNotEqual(parents[1].source_ref, parents[0].source_ref)

    def test_logging_failure_cannot_interrupt_real_generation_and_document_registration(self) -> None:
        self.enable_monitor()
        with patch.object(self.app.state.monitor.store, "append", side_effect=OSError("diagnostic disk unavailable")), \
                self.assertLogs("archflow_studio_api.application.monitoring", level="WARNING"):
            generated = self.generate()
        self.assertEqual(generated.status_code, 201, generated.text)
        with TestClient(create_app(self.settings)) as reopened:
            result = reopened.get("/api/documents", params={"runId": self.model["runId"]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIn(generated.json(), result.json()["documents"])
            data = reopened.get(f"/api/documents/{generated.json()['assetSha256']}/bytes", params={
                "runId": self.model["runId"], "revisionRef": generated.json()["revisionRef"],
            })
            self.assertEqual(data.status_code, 200, data.text[:200])
            self.assertTrue(data.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.repository.read_head(), self.head)

    def test_unavailable_cached_drawing_records_a_refusal_without_rebuilding(self) -> None:
        self.enable_monitor()
        generated = self.generate()
        self.assertEqual(generated.status_code, 201, generated.text)
        drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(generated.json()["revisionRef"], PROJECT_ID))
        self.repository.layout.resolve_record(drawing.png_ref).write_bytes(drawing.png + b"changed")
        with patch("archflow_studio_api.application.drawings.freeze_model_axis_elevation",
                   side_effect=AssertionError("an unavailable cached revision must not be replaced")):
            refused = self.generate()
        self.assertEqual(refused.status_code, 409, refused.text)
        parent = [event for event in self.drawing_events() if event.phase == "drawing_generate"][-1]
        self.assertEqual(parent.status, "failed")
        self.assertEqual(parent.details["cache_status"], "refused")
        self.assertEqual(parent.details["cache_reason"], "cached_document_unavailable")
        self.assertEqual(parent.details["cache_checks"]["bytes"], "invalid")
        self.assertEqual(parent.details["executed_stages"], [])

    def save_drawing_page(self, drawing: dict, comment: str, *, pinned: bool = True) -> dict:
        response = self.client.put("/api/document-annotations", json={
            "projectId": PROJECT_ID, "runId": drawing["runId"], "assetSha256": drawing["assetSha256"],
            "pageIndex": 0, "baseRevisionSha256": None, "annotations": [], "comment": comment,
            **({"drawingRevisionRef": drawing["revisionRef"]} if pinned else {}),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def intent_from_drawing(self, drawing: dict, saved: dict):
        reference = {key: saved[key] for key in ("runId", "assetSha256", "pageIndex", "revisionSha256")}
        if saved.get("drawingRevisionRef") is not None:
            reference["drawingRevisionRef"] = saved["drawingRevisionRef"]
        data = self.client.get(f"/api/documents/{drawing['assetSha256']}/bytes", params={
            "runId": drawing["runId"], "revisionRef": drawing["revisionRef"],
        })
        self.assertEqual(data.status_code, 200, data.text[:200])
        output = BytesIO()
        with Image.open(BytesIO(data.content)) as image:
            image.thumbnail((2048, 2048))
            image.save(output, format="PNG")
        return self.client.post("/api/intents", json={
            "projectId": PROJECT_ID, "sourceRunId": self.model["runId"], "stateDigest": self.model["stateDigest"],
            "sourceStageRef": self.stage["stageRef"], "modelSource": self.model,
            "utterance": "set height to 2.4", "targetComponentId": "portico", "elementId": "portico-base",
            "documentAnnotations": [reference], "documentVisuals": [{
                "role": "edit", **reference, "pagePngBase64": base64.b64encode(output.getvalue()).decode(),
                "annotatedPngBase64": None,
            }],
        })

    def test_same_png_drawing_revisions_have_independent_annotation_heads(self) -> None:
        first, second = self.generate(drawingId="elevation-a").json(), self.generate(drawingId="elevation-b").json()
        self.assertEqual(first["assetSha256"], second["assetSha256"])
        self.assertNotEqual(first["revisionRef"], second["revisionRef"])
        listing = self.client.get("/api/documents")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertIsNone(listing.json()["runId"])
        self.assertCountEqual(listing.json()["documents"], [first, second])
        a = self.save_drawing_page(first, "the first drawing")
        b = self.save_drawing_page(second, "the second drawing")
        for drawing, saved in ((first, a), (second, b)):
            query = {"runId": drawing["runId"], "assetSha256": drawing["assetSha256"], "pageIndex": 0,
                     "drawingRevisionRef": drawing["revisionRef"]}
            self.assertEqual(self.client.get("/api/document-annotations", params=query).json(), saved)
        wrong = self.client.get("/api/document-annotations", params={**query, "revisionSha256": a["revisionSha256"]})
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.json()["code"], "ANNOTATION_REVISION_NOT_FOUND")
        response = self.intent_from_drawing(first, a)
        self.assertEqual(response.status_code, 201, response.text)
        comment = self.repository.load_json(record_ref_from_uri(response.json()["documentCommentRef"], PROJECT_ID))
        self.assertEqual(comment["documentAnnotations"][0]["drawingRevisionRef"], first["revisionRef"])
        self.assertEqual(comment["documentVisuals"][0]["drawingRevisionRef"], first["revisionRef"])
        self.assertEqual(comment["documentSources"][0]["drawingRevisionRef"], first["revisionRef"])

    def test_newer_identical_drawing_does_not_rebind_new_or_legacy_submitted_proposals(self) -> None:
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                first = self.generate(drawingId=f"elevation-a-{pinned}").json()
                saved = self.save_drawing_page(first, "keep this drawing source", pinned=pinned)
                response = self.intent_from_drawing(first, saved)
                self.assertEqual(response.status_code, 201, response.text)
                answer = response.json()
                comment_ref = record_ref_from_uri(answer["documentCommentRef"], PROJECT_ID)
                comment = self.repository.load_json(comment_ref)
                before = self.repository.layout.resolve_record(comment_ref).read_bytes()
                association = self.repository.load_json(record_ref_from_uri(comment["documentSources"][0]["bindingRef"], PROJECT_ID))
                self.assertEqual(association["revisionRef"], first["revisionRef"])
                if not pinned:
                    self.assertNotIn("drawingRevisionRef", comment["documentAnnotations"][0])
                    self.assertNotIn("drawingRevisionRef", comment["documentSources"][0])
                    self.assertNotIn("drawingRevisionRef", comment["documentVisuals"][0])
                newer = self.generate(drawingId=f"elevation-b-{pinned}").json()
                self.assertEqual(newer["assetSha256"], first["assetSha256"])
                self.assertNotEqual(newer["revisionRef"], first["revisionRef"])
                with TestClient(create_app(self.settings)) as reopened:
                    binding = bound_project(reopened.app.state)
                    projection = project_state(binding, source_stage_ref=self.stage["stageRef"])
                    require_document_comment_source(binding, binding.repository.load_json(comment_ref), projection)
                accepted = self.start(answer["proposal"]["proposalId"])
                self.assertEqual(self.finished(accepted["jobId"])["status"], "succeeded")
                self.assertEqual(self.repository.layout.resolve_record(comment_ref).read_bytes(), before)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_real_elevation_revisions_reopen_with_their_own_stage_and_bytes(self) -> None:
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        r1 = first.json()
        self.assertEqual(r1["modelSource"], self.model)
        self.assertEqual(r1["sourceStageRef"], self.stage["stageRef"])
        self.assertEqual(r1["viewRecipe"]["look"], [0, 1, 0])
        self.assertEqual(self.generate().json(), r1)
        accepted, job = self.run_candidate(
            "set height to 3.5", elementId="portico-base", sourceRunId=self.stage["candidateId"],
            stateDigest=self.model["stateDigest"], sourceStageRef=self.stage["stageRef"],
        )
        self.assertEqual(job["status"], "succeeded", job)
        committed = self.client.post(f"/api/candidates/{accepted['candidateId']}/accept", json={
            "projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": self.stage["stageRef"],
        })
        self.assertEqual(committed.status_code, 200, committed.text)
        second = self.generate(committed.json())
        self.assertEqual(second.status_code, 201, second.text)
        r2 = second.json()
        self.assertEqual(r1["drawingId"], r2["drawingId"])
        self.assertNotEqual(r1["revisionRef"], r2["revisionRef"])
        self.assertNotEqual(r1["assetSha256"], r2["assetSha256"])
        with TestClient(create_app(self.settings)) as reopened:
            for revision in (r1, r2):
                listed = reopened.get("/api/documents", params={"runId": revision["runId"]})
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertIn(revision, listed.json()["documents"])
                data = reopened.get(f"/api/documents/{revision['assetSha256']}/bytes", params={
                    "runId": revision["runId"], "revisionRef": revision["revisionRef"],
                })
                self.assertEqual(data.status_code, 200, data.text[:200])
                drawing = read_model_axis_elevation(self.repository, record_ref_from_uri(revision["revisionRef"], PROJECT_ID))
                self.assertEqual(data.content, drawing.png)
                self.assertEqual(drawing.receipt["source"]["run_id"], revision["modelSource"]["runId"])
                self.assertEqual(drawing.run.base, self.head)
            wrong = reopened.get(f"/api/documents/{r1['assetSha256']}/bytes", params={"runId": r1["runId"], "revisionRef": r2["revisionRef"]})
            self.assertEqual(wrong.status_code, 404)
        self.assertEqual(self.generate().json(), r1)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_imported_complete_model_cannot_use_native_components_as_whole_building(self) -> None:
        imported = register_model(self.client, self.model["runId"], self.model["stateDigest"],
                                  (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes())
        composed = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID,
                                   "branchId": "imported", "modelSource": imported["modelSource"]})
        self.assertEqual(composed.status_code, 201, composed.text)
        result = self.generate(composed.json())
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        observation = self.client.get("/api/drawings/model-view", params=imported["modelSource"])
        self.assertEqual(observation.status_code, 409, observation.text)
        self.assertEqual(observation.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_stage_uses_pinned_runner_and_view_recipe_does_not_overwrite(self) -> None:
        retain_runner_receipt(self.repository, self.repository.load_run(self.stage["candidateId"]), design_state_digest=None)
        first = self.generate()
        self.assertEqual(first.status_code, 201, first.text)
        other = self.generate(view="right")
        self.assertEqual(other.status_code, 201, other.text)
        self.assertNotEqual(first.json()["revisionRef"], other.json()["revisionRef"])
        self.assertEqual(other.json()["viewRecipe"]["look"], [-1, 0, 0])
        documents = self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
        self.assertEqual(len(documents), 2)

    def test_unavailable_exact_step_is_not_rebuilt_or_replaced_by_another_run(self) -> None:
        (self.repository.layout.root / self.step["relativePath"]).write_bytes(b"changed STEP")
        response = self.generate()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "DRAWING_COMPLETE_SOURCE_UNAVAILABLE")
        self.assertEqual(self.client.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], [])

    def test_generation_order_and_original_time_survive_cold_idempotent_reads(self) -> None:
        times = (datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc), datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc))
        with patch("archflow_studio_api.application.drawings.datetime") as clock:
            clock.now.side_effect = times
            first = self.generate()
            self.assertEqual(first.status_code, 201, first.text)
            second = self.generate(scaleDenominator=50, hiddenLines=True)
            self.assertEqual(second.status_code, 201, second.text)
            r1, r2 = first.json(), second.json()
            self.assertEqual(r1["sourceStageRef"], r2["sourceStageRef"])
            self.assertEqual(r1["drawingId"], r2["drawingId"])
            self.assertNotEqual(r1["revisionRef"], r2["revisionRef"])
            self.assertEqual([r1["generatedAt"], r2["generatedAt"]], [time.isoformat() for time in times])
            uploaded = self.client.post("/api/documents", json={
                "projectId": PROJECT_ID, "runId": self.model["runId"], "fileName": "reference.png", "mimeType": "image/png",
                "contentBase64": base64.b64encode(image_bytes()).decode(),
            })
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertIsNone(uploaded.json()["generatedAt"])
            with TestClient(create_app(self.settings)) as reopened:
                docs = reopened.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"]
                self.assertEqual(docs, [r2, r1, uploaded.json()])
                repeated = reopened.post("/api/drawings/elevations", json={
                    "projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "view": "front", "drawingId": "main-elevation",
                })
                self.assertEqual(repeated.status_code, 201, repeated.text)
                self.assertEqual(repeated.json(), r1)
                self.assertEqual(reopened.get("/api/documents", params={"runId": self.model["runId"]}).json()["documents"], docs)
