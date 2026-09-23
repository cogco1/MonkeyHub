"""Semantic door jambs are measured from the exact STEP, not dimension text."""

from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.adapters import occt_backend
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.artifacts import ModelSource
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.drawing_dimensions import (
    list_plan_dimension_intents, resolve_plan_dimensions,
)
from archflow_studio_api.application.drawings import _complete_source
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeydiagram.drawing_elevation import ElevationView, read_elevation_source

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase


INTENT = {"id": "door-width", "entityRef": "entity:door-wall", "openingId": "entry",
          "placement": {"offsetMm": 8}}


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class DrawingDimensionTests(CandidateTestCase):
    def setUp(self):
        super().setUp()
        self.client.close()
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="occt")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.binding = bound_project(self.app.state)
        self.frame = ElevationView("plan", (0, 0, 1.2), (0, 0, -1), (1, 0, 0), (0, 1, 0),
                                   (-10, -10, 10, 10), 0, 1.2)

    def source(self, *, width="@module", openings=None, line=None, parameters=None):
        opening = {"opening_id": "entry", "kind": "door", "along": 2, "width": width, "sill": 0, "head": 2.1}
        edit = {"summary": "Retain a door opening fixture.", "entities": [{
            "entity_id": "door-wall", "schema": "Element@1", "parent_id": "portico",
            "fields": {"component_id": "portico", "producer": "wall",
                       "references": {"base": {"level": "level-ground"}, "line": line or {
                           "from": {"point": [1, 3]}, "to": {"point": [5, 3]}}},
                       "params": {"height": 3, "thickness": 0.2, "openings": [opening] if openings is None else openings}},
            "basis_refs": ["evidence:demo"],
        }]}
        if parameters:
            edit["parameters"] = parameters
        response = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "sourceRunId": REFERENCE_RUN_ID, "stateDigest": self.state_digest, "semanticEdit": edit})
        self.assertEqual(response.status_code, 201, response.text)
        accepted = self.start(response.json()["proposalId"])
        job = self.finished(accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        model = ModelSource.from_dict(next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm"))
        source, _ = _complete_source(self.binding, model, None)
        return model, read_elevation_source(self.repository, source)

    def resolve(self, model, verified, **kwargs):
        return resolve_plan_dimensions(self.binding, model, None, verified, kwargs.get("frame", self.frame),
                                       kwargs.get("dimensions", (INTENT,)), hidden_object_ids=kwargs.get("hidden_object_ids", ()))

    def test_exact_jamb_measurement_uses_producer_semantics_and_preserves_readonly_source(self):
        model, source = self.source()
        before = {p.relative_to(self.repository.layout.root): p.read_bytes()
                  for p in self.repository.layout.root.rglob("*") if p.is_file()}
        result, = self.resolve(model, source, dimensions=({**INTENT, "value": 999, "label": "fake",
                                                         "parameterKey": "plinth"},))
        self.assertEqual(result["status"], "resolved", result)
        self.assertAlmostEqual(result["value"], 1.2)
        self.assertEqual(result["label"], "1200 mm")
        for actual, expected in zip(result["start"], (2.4, 3)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(result["end"], (3.6, 3)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual((result["parameterKey"], result["parameterUnit"], result["canDrive"]), ("module", "m", True))
        choices = list_plan_dimension_intents(self.binding, model, None)
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]["label"], "door-wall / entry")
        self.assertNotIn("value", choices[0])
        moved, = self.resolve(model, source, dimensions=({**INTENT, "placement": {"offsetMm": -14}},))
        self.assertEqual({k: v for k, v in moved.items() if k != "offsetMm"},
                         {k: v for k, v in result.items() if k != "offsetMm"})
        self.assertEqual(moved["offsetMm"], -14)
        after = {p.relative_to(self.repository.layout.root): p.read_bytes()
                 for p in self.repository.layout.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before, "dimension resolution and paper placement never write Design or documents")

    def test_literal_derived_and_locked_widths_measure_without_fake_driving(self):
        for width, reason in ((1.2, "direct"), ("@bay", "derived"), ("@plinth", "locked")):
            with self.subTest(width=width):
                model, source = self.source(width=width)
                dimension, = self.resolve(model, source)
                self.assertEqual(dimension["status"], "resolved", dimension)
                self.assertFalse(dimension["canDrive"])
                self.assertIn(reason, dimension["driveReason"])
                if isinstance(width, float):
                    self.assertNotIn("parameterKey", dimension)

    def test_perpendicular_host_and_reversed_inward_are_measured_in_the_cad_frame_once(self):
        for inward in ([1, 0], [-1, 0]):
            with self.subTest(inward=inward):
                model, source = self.source(line={"from": {"point": [1, 2]}, "to": {"point": [1, 6]}, "inward": inward})
                result, = self.resolve(model, source)
                self.assertEqual(result["status"], "resolved", result)
                self.assertAlmostEqual(result["value"], 1.2)
                self.assertAlmostEqual(abs(result["end"][0] - result["start"][0]), 0)
                self.assertAlmostEqual(abs(result["end"][1] - result["start"][1]), 1.2)

    def test_removed_wall_opening_cut_height_and_crop_are_explicit_broken_anchors(self):
        model, source = self.source()
        for intent in ({**INTENT, "entityRef": "entity:removed"}, {**INTENT, "openingId": "removed"}):
            result, = self.resolve(model, source, dimensions=(intent,))
            self.assertEqual(result["status"], "missing")
            self.assertNotIn("value", result)
            self.assertFalse(result["canDrive"])
        for frame in (replace(self.frame, origin=(0, 0, 2.5)), replace(self.frame, crop_uv=(-1, -1, 1, 1))):
            result, = self.resolve(model, source, frame=frame)
            self.assertEqual(result["status"], "outside-view")
            self.assertNotIn("start", result)
        removed, removed_source = self.source(openings=[])
        result, = self.resolve(removed, removed_source)
        self.assertEqual(result["status"], "missing")
        # The retained old source remains a valid measurement after a successor.
        old, = self.resolve(model, source)
        self.assertEqual(old["status"], "resolved")

    def test_hidden_semantic_wall_does_not_emit_an_ordinary_dimension(self):
        model, source = self.source()
        with patch("archflow_studio_api.application.drawing_dimensions.section_occt_lines",
                   side_effect=AssertionError("hidden anchors do not need a section solve")):
            result, = self.resolve(model, source, hidden_object_ids=("obj-door-wall-cut",))
        self.assertEqual(result["status"], "outside-view")
        self.assertNotIn("value", result)
        self.assertFalse(result["canDrive"])

    def test_paper_placement_must_fit_the_crop_including_strokes_and_text(self):
        model, source = self.source()
        frame = replace(self.frame, crop_uv=(0, 0, 6, 5))
        fitting, = self.resolve(model, source, frame=frame)
        self.assertEqual(fitting["status"], "resolved", fitting)
        self.assertEqual(fitting["label"], "1200 mm")
        for intent, crop in (({**INTENT, "placement": {"offsetMm": 100}}, frame.crop_uv),
                             (INTENT, (1, 2.7, 5, 3.2))):
            with self.subTest(offset=intent["placement"]["offsetMm"], crop=crop):
                outside, = self.resolve(model, source, frame=replace(frame, crop_uv=crop), dimensions=(intent,))
                self.assertEqual(outside["status"], "outside-view", outside)
                self.assertFalse(outside["canDrive"])
                self.assertTrue(all(key not in outside for key in ("value", "label", "start", "end", "parameterKey")))
                self.assertIn("paper offset or crop", outside["detail"])
        # Repairing the placement restores the same exact measured dimension.
        self.assertEqual(self.resolve(model, source, frame=frame), (fitting,))

    def test_source_proof_missing_or_mixed_refuses_measurement(self):
        model, source = self.source()
        other, other_source = self.source(width=1.5)
        bad_receipt = {**source.receipt, "identity": {**source.receipt["identity"], "binding": {
            **source.receipt["identity"]["binding"], "design_state_digest": "0" * 64}}}
        for supplied_model, supplied_source in ((other, source), (model, other_source),
                                                (replace(model, asset_sha256="0" * 64), source),
                                                (model, replace(source, receipt=bad_receipt))):
            with self.subTest(model=supplied_model), patch(
                "archflow_studio_api.application.drawing_dimensions.section_occt_lines",
                side_effect=AssertionError("mismatched proof cannot measure"),
            ):
                result, = self.resolve(supplied_model, supplied_source)
                self.assertEqual(result["status"], "unverified")
                self.assertNotIn("value", result)

    def test_exact_stage_dimension_reopens_and_refuses_a_different_model(self):
        model, source = self.source()
        response = self.client.post("/api/design-stages/initialize", json={
            "projectId": PROJECT_ID, "modelSource": model.to_dict(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        stage_ref = record_ref_from_uri(response.json()["stageRef"], PROJECT_ID)
        original = resolve_plan_dimensions(self.binding, model, stage_ref, source, self.frame, (INTENT,))
        self.assertEqual(original[0]["status"], "resolved", original)
        with TestClient(create_app(self.settings)) as reopened:
            cold_binding = bound_project(reopened.app.state)
            elevation, _ = _complete_source(cold_binding, model, stage_ref)
            cold = read_elevation_source(cold_binding.repository, elevation)
            self.assertEqual(resolve_plan_dimensions(cold_binding, model, stage_ref, cold, self.frame, (INTENT,)), original)
        mismatched = replace(model, asset_sha256="f" * 64)
        bad, = resolve_plan_dimensions(self.binding, mismatched, stage_ref, source, self.frame, (INTENT,))
        self.assertEqual(bad["status"], "unverified")

    def test_repeated_or_shaped_openings_are_not_silently_bound_as_one_rectangle(self):
        for changes in ({"count": 2, "step": 1.0, "width": 0.6},
                        {"shape": "semicircular_arch", "spring_height": 1.5}):
            opening = {"opening_id": "entry", "kind": "door", "along": 2, "width": 1.2, "sill": 0, "head": 2.1, **changes}
            with self.subTest(changes=changes):
                model, source = self.source(openings=[opening])
                result, = self.resolve(model, source)
                self.assertEqual(result["status"], "unverified")
                self.assertNotIn("value", result)
                self.assertEqual(list_plan_dimension_intents(self.binding, model, None), ())

    def test_missing_and_duplicate_jambs_never_rebind_to_nearest_segments(self):
        model, source = self.source()
        lines = occt_backend.section_occt_lines(source.entries, object_ids=("obj-door-wall-cut",),
            origin=self.frame.origin, right=self.frame.right, up=self.frame.up, linear_deflection=self.frame.linear_deflection)
        for replacement, expected in (((), "missing"), (lines + lines, "ambiguous"),
            (tuple(replace(line, points=tuple((x + 0.01, y) for x, y in line.points)) for line in lines), "missing")):
            with self.subTest(expected=expected), patch(
                "archflow_studio_api.application.drawing_dimensions.section_occt_lines", return_value=replacement,
            ):
                result, = self.resolve(model, source)
                self.assertEqual(result["status"], expected)
                self.assertNotIn("value", result)

    def test_real_step_jambs_are_required_even_when_the_parameters_match(self):
        model, source = self.source()
        # Use the receipt's same object identity but a filled wall shape: its
        # outside bounds and the source width parameter still match.
        _, filled_source = self.source(openings=[])
        filled = next(entry.shape for entry in filled_source.entries if entry.name == "obj-door-wall")
        entries = tuple(replace(entry, shape=filled) if entry.name == "obj-door-wall-cut" else entry for entry in source.entries)
        result, = self.resolve(model, replace(source, entries=entries))
        self.assertEqual(result["status"], "missing")
        self.assertNotIn("value", result)

    def test_parameter_unit_disagreement_keeps_real_measurement_but_disables_driving(self):
        model, source = self.source(width="@mm_width", parameters=[{"key": "mm_width", "value": 1.2, "unit": "mm"}])
        result, = self.resolve(model, source)
        self.assertEqual(result["status"], "resolved", result)
        self.assertEqual(result["label"], "1200 mm")
        self.assertFalse(result["canDrive"])
        self.assertIn("unit/value", result["driveReason"])

    def test_exact_step_units_convert_model_coordinates_once_and_keep_paper_offset_mm(self):
        from archflow_studio_api.application.drawing_dimensions import _source_projection

        model, source = self.source()
        projection, artifact = _source_projection(self.binding, model, None)
        elevation, _ = _complete_source(self.binding, model, None)
        step_path = self.repository.layout.root / elevation.step_relative_path
        # Re-read the actual STEP in each adapter-supported output unit. The
        # stub is only the already-verified source boundary; section and jamb
        # resolution use the real cold-read B-reps at every unit.
        for unit, factor in (("millimeter", 1000), ("foot", 1 / .3048), ("inch", 1 / .0254)):
            receipt = {**source.receipt, "identity": {**source.receipt["identity"], "length_unit": unit}}
            converted = replace(source, receipt=receipt, length_unit=unit,
                                entries=occt_backend.read_step(step_path, length_unit=unit))
            frame = replace(self.frame, origin=(0, 0, 1.2 * factor),
                            crop_uv=tuple(v * factor for v in self.frame.crop_uv), far_depth=1.2 * factor)
            with self.subTest(unit=unit), patch(
                "archflow_studio_api.application.drawing_dimensions._source_projection", return_value=(projection, artifact),
            ), patch.object(self.binding.repository, "load_json", return_value=receipt):
                result, = self.resolve(model, converted, frame=frame)
                self.assertEqual(result["status"], "resolved", result)
                self.assertAlmostEqual(result["value"], 1.2 * factor)
                self.assertEqual(result["label"], "1200 mm")
                self.assertEqual(result["offsetMm"], 8)
                self.assertTrue(result["canDrive"])
