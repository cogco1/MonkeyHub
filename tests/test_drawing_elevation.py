"""One model-axis elevation frozen from a retained exact STEP through P036, and read back cold."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from archflow.adapters import occt_backend
from monkeydiagram import drawing_elevation
from monkeydiagram.drawing_svg import render_svg_png, svg_objects
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DRAWING_PROJECTION_RECEIPT, SEAT_OCCT_EXECUTION
from archflow.project.repository import FilesystemProjectRepository
from monkeydiagram.drawing_elevation import (
    DrawingElevationError,
    ElevationSource,
    ElevationView,
    freeze_model_axis_elevation,
    freeze_cut_plan,
    list_model_axis_elevations,
    project_model_axis_elevation,
    read_model_axis_elevation,
)

NEEDS_OCCT = unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
PROJECT_ID = "drawing-project"
SOURCE_RUN = "source-run"
STAGE = "seat-demo"
STEP_NAME = "seat-demo@abc123.step"
WORKSPACE = "cad-seat-demo"
OBJECTS = ("wall", "rear", "post", "far", "skin")


def _shapes() -> dict[str, object]:
    """A wall at depth 2..3, a box wholly behind it, a box beside it, a box far away, and an open face."""

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    polygon = BRepBuilderAPI_MakePolygon()
    for x, y, z in ((-2, 2, 0), (-1, 2, 0), (-1, 2, 2), (-2, 2, 2)):
        polygon.Add(gp_Pnt(x, y, z))
    polygon.Close()
    return {
        "wall": BRepPrimAPI_MakeBox(gp_Pnt(0, 2, 0), 4, 1, 3).Shape(),
        "rear": BRepPrimAPI_MakeBox(gp_Pnt(1, 5, 1), 1, 1, 1).Shape(),
        "post": BRepPrimAPI_MakeBox(gp_Pnt(6, 2, 0), 1, 1, 3).Shape(),
        "far": BRepPrimAPI_MakeBox(gp_Pnt(1, 20, 4), 1, 1, 1).Shape(),
        "skin": BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face(),
    }


SVG_NS = "{http://www.w3.org/2000/svg}"


def _group_lines(svg: bytes, group: str) -> list[tuple[str, list[tuple[float, float]]]]:
    """Each polyline of one SVG group: its object and points, in SVG user units."""

    root = ElementTree.fromstring(svg)
    return [(line.get("data-object"), [tuple(float(v) for v in pair.split(",")) for pair in line.get("points").split()])
            for line in root.findall(f"{SVG_NS}g[@id='{group}']/{SVG_NS}polyline")]


def _lies_on(points, segments, tolerance: float) -> bool:
    """Independently of the cleanup: every sampled point of the line is within the tolerance of some segment."""

    def distance(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1.0)))
        return math.dist(p, (a[0] + t * dx, a[1] + t * dy))

    samples = [(a[0] + (b[0] - a[0]) * i / 40, a[1] + (b[1] - a[1]) * i / 40)
               for a, b in zip(points, points[1:]) for i in range(41)]
    return all(any(distance(p, a, b) <= tolerance for a, b in segments) for p in samples)


def _view(**overrides) -> ElevationView:
    values = dict(
        name="model-minus-y-elevation", origin=(0.0, 0.0, 0.0), look=(0.0, 1.0, 0.0), right=(1.0, 0.0, 0.0),
        up=(0.0, 0.0, 1.0), crop_uv=(-3.0, -1.0, 5.0, 6.0), near_depth=0.0, far_depth=30.0,
    )
    return ElevationView(**{**values, **overrides})


@NEEDS_OCCT
class ProjectionValueTests(unittest.TestCase):
    """The in-memory projection: frame, crop, near/far and the hidden-line switch, with no repository."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / STEP_NAME
        occt_backend.write_step(path, tuple(occt_backend.StepObject(name, shape, "layer") for name, shape in _shapes().items()),
                                length_unit="meter")
        cls.entries = occt_backend.read_step(path, length_unit="meter")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def project(self, view: ElevationView):
        return project_model_axis_elevation(self.entries, object_ids=OBJECTS, view=view, unit="meter")

    def test_every_object_takes_part_and_only_what_is_seen_inside_the_window_is_drawn(self) -> None:
        result = self.project(_view())
        self.assertEqual(result, self.project(_view()), "the same source and frame give the same bytes")
        counts = result.counts()
        self.assertEqual({line.object_id for line in result.lines}, set(OBJECTS), "every object took part in the solve")
        self.assertEqual(counts["objects_with_hidden_lines"], 4, "a single open face has no edge behind itself")
        self.assertIn("rear", {line.object_id for line in result.lines if line.kind == "hidden"})
        self.assertNotIn("rear", {line.object_id for line in result.lines if line.kind == "visible"}, "wholly behind the wall")
        self.assertTrue(any(line.object_id == "post" and line.kind == "visible" for line in result.lines), "solved, but outside the crop")
        self.assertEqual(svg_objects(result.svg), ("far", "skin", "wall"))
        self.assertEqual(counts["objects_drawn_in_svg"], 3)
        self.assertTrue(result.png.startswith(b"\x89PNG"))

    def test_the_far_plane_removes_what_lies_beyond_it(self) -> None:
        self.assertEqual(svg_objects(self.project(_view(far_depth=10.0)).svg), ("skin", "wall"))

    def test_hidden_lines_draw_the_occluded_object_when_asked(self) -> None:
        self.assertEqual(svg_objects(self.project(_view(hidden_lines=True)).svg), ("far", "rear", "skin", "wall"),
                         "the occluded box appears dashed; the box outside the crop still does not")

    def test_top_projection_uses_plan_width_and_depth_without_a_section_cut(self) -> None:
        top = _view(name="elevation-top", look=(0.0, 0.0, -1.0), right=(1.0, 0.0, 0.0),
                    up=(0.0, 1.0, 0.0), crop_uv=(-3.0, 1.0, 8.0, 22.0), near_depth=-6.0, far_depth=1.0)
        result = self.project(top)
        self.assertEqual(result, self.project(top))
        # Distinct XY rectangles at three elevations all remain visible from above.
        for object_id, expected in (("wall", (0.0, 2.0, 4.0, 3.0)),
                                    ("rear", (1.0, 5.0, 2.0, 6.0)),
                                    ("far", (1.0, 20.0, 2.0, 21.0))):
            with self.subTest(object_id=object_id):
                points = [point for line in result.lines if line.object_id == object_id and line.kind == "visible"
                          for point in line.points]
                actual = (min(p[0] for p in points), min(p[1] for p in points),
                          max(p[0] for p in points), max(p[1] for p in points))
                for measured, stated in zip(actual, expected):
                    self.assertAlmostEqual(measured, stated, places=6)
                self.assertIn(object_id, svg_objects(result.svg))
        self.assertTrue(result.png.startswith(b"\x89PNG"))

    def test_an_inconsistent_or_degenerate_frame_is_refused(self) -> None:
        for overrides in (
            dict(look=(0.0, -1.0, 0.0)), dict(right=(2.0, 0.0, 0.0)), dict(up=(1.0, 0.0, 0.0)),
            dict(crop_uv=(0.0, 0.0, 0.0, 1.0)), dict(near_depth=5.0, far_depth=5.0), dict(linear_deflection=0.0),
            dict(scale_denominator=0), dict(name="not a name"), dict(hidden_lines=1),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(DrawingElevationError):
                _view(**overrides)
        with self.assertRaises(DrawingElevationError):
            project_model_axis_elevation(self.entries, object_ids=OBJECTS + ("ghost",), view=_view(), unit="meter")


@NEEDS_OCCT
class FreezeElevationTests(unittest.TestCase):
    """The P036 boundary: a drawing run on the source base, files and receipt, cold readback, HEAD untouched."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"phase": "request", "commitments": []},
        )
        self.source_run = self.repository.create_run(SOURCE_RUN)
        # The CAD adapter writes the STEP into the run's speculative workspace itself; mirror that placement.
        workspace = self.repository.layout.run(SOURCE_RUN).workspaces / WORKSPACE
        workspace.mkdir()
        occt_backend.write_step(workspace / STEP_NAME,
                                tuple(occt_backend.StepObject(name, shape, "layer") for name, shape in _shapes().items()),
                                length_unit="meter")
        self.step_sha = hashlib.sha256((workspace / STEP_NAME).read_bytes()).hexdigest()
        self.receipt_ref = self.repository.put_json(
            run=self.source_run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
            record_kind=SEAT_OCCT_EXECUTION, payload=self.source_receipt(self.step_sha),
        )
        self.source = ElevationSource(
            run_id=SOURCE_RUN, step_relative_path=f"runs/{SOURCE_RUN}/workspaces/{WORKSPACE}/{STEP_NAME}",
            step_sha256=self.step_sha,
            cad_receipt_relative_path=self.receipt_ref.relative_path, cad_receipt_sha256=self.receipt_ref.sha256,
        )
        self.head = self.repository.read_head()

    def source_receipt(self, step_sha256: str, **overrides) -> dict:
        payload = {
            "schema": "OcctExecutionReceipt@1", "status": "succeeded", "readback_verified": True,
            "identity": {
                "schema": "OcctCadExportIdentity@1", "length_unit": "meter", "up_axis": "Z-up",
                "binding": {"project_id": PROJECT_ID, "run_id": SOURCE_RUN, "base": self.source_run.base.to_dict(),
                            "program_digest": "1" * 64, "stage_id": STAGE},
            },
            "exact_artifact": {"relative_path": STEP_NAME, "sha256": step_sha256, "exact_brep": True,
                               "deliveries": {name: "closed_solid" for name in OBJECTS}},
            "physical_object_ids": sorted(OBJECTS),
        }
        payload.update(overrides)
        return payload

    def test_freezes_svg_png_and_receipt_in_a_drawing_run_on_the_source_base_and_reads_them_back_cold(self) -> None:
        observations = []
        drawing = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run",
                                             operation_observer=observations.append, parent_event_id="drawing-operation")
        completed = [event for event in observations if event["status"] == "succeeded"]
        self.assertEqual([event["phase"] for event in completed],
                         ["drawing.load", "drawing.hlr", "drawing.svg", "drawing.png", "drawing.persist"])
        self.assertEqual(len(observations), 2 * len(completed))
        for event in completed:
            begun = next(row for row in observations if row["event_id"] == event["event_id"] and row["status"] == "running")
            self.assertIsNone(begun["duration_ms"])
            self.assertIsNone(begun["ended_at"])
            self.assertGreaterEqual(datetime.fromisoformat(event["ended_at"]), datetime.fromisoformat(event["started_at"]))
            self.assertGreaterEqual(event["duration_ms"], 0)
            self.assertEqual(event["parent_event_id"], "drawing-operation")
            self.assertEqual(event["details"]["input_identity"]["step_sha256"], self.step_sha)
        hlr = next(event for event in completed if event["phase"] == "drawing.hlr")
        svg = next(event for event in completed if event["phase"] == "drawing.svg")
        self.assertEqual(hlr["details"]["input_object_ids"], sorted(OBJECTS))
        self.assertEqual(hlr["details"]["scope"], "global_visibility")
        self.assertEqual(svg["details"]["emitted_object_ids"], list(svg_objects(drawing.svg)))
        self.assertNotEqual(hlr["details"]["input_object_ids"], svg["details"]["emitted_object_ids"])
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(drawing.run.base, self.source_run.base)
        receipt = drawing.receipt
        self.assertEqual(receipt["schema"], "DrawingProjectionReceipt@1")
        self.assertEqual(receipt["source"]["step"]["sha256"], self.step_sha)
        self.assertEqual(receipt["source"]["cad_receipt"]["sha256"], self.receipt_ref.sha256)
        self.assertEqual(receipt["source"]["base"], self.source_run.base.to_dict())
        self.assertEqual(receipt["source"]["physical_object_ids"], sorted(OBJECTS))
        self.assertEqual(receipt["view"]["look"], [0.0, 1.0, 0.0])
        self.assertEqual(receipt["view"]["crop_uv"], [-3.0, -1.0, 5.0, 6.0])
        self.assertEqual((receipt["projection"]["object_count"], receipt["projection"]["objects_drawn_in_svg"]), (5, 3))
        self.assertEqual(receipt["artifacts"]["svg"]["relative_path"], "runs/drawing-run/workspaces/documentation/model-minus-y-elevation.svg")
        self.assertEqual(receipt["artifacts"]["svg"]["sha256"], hashlib.sha256(drawing.svg).hexdigest())
        self.assertEqual(receipt["artifacts"]["png"]["sha256"], hashlib.sha256(drawing.png).hexdigest())
        self.assertEqual(svg_objects(drawing.svg), ("far", "skin", "wall"))
        self.assertNotIn("timestamp", json.dumps(receipt))

        reopened = FilesystemProjectRepository.open(self.root)
        (receipt_ref,) = list_model_axis_elevations(reopened, "drawing-run")
        self.assertEqual(receipt_ref, drawing.receipt_ref)
        cold = read_model_axis_elevation(reopened, receipt_ref)
        self.assertEqual((cold.svg, cold.png, cold.receipt), (drawing.svg, drawing.png, receipt))
        self.assertEqual(reopened.load_run("drawing-run").base, self.source_run.base)
        self.assertEqual(reopened.read_head(), self.head)

        with patch.object(drawing_elevation, "project_occt_lines", wraps=drawing_elevation.project_occt_lines) as project:
            again = freeze_model_axis_elevation(reopened, source=self.source, view=_view(), drawing_run_id="drawing-run",
                                                operation_observer=observations.append, parent_event_id="repeated-operation")
        self.assertEqual(project.call_count, 1)
        self.assertTrue(any(event["phase"] == "drawing.hlr" and event["parent_event_id"] == "repeated-operation"
                            and event["status"] == "succeeded" for event in observations))
        self.assertEqual((again.receipt_ref, again.svg_ref, again.png_ref), (drawing.receipt_ref, drawing.svg_ref, drawing.png_ref))
        self.assertEqual(len(list_model_axis_elevations(reopened, "drawing-run")), 1)

    def test_observer_failure_cannot_change_drawing_or_hide_a_source_refusal(self) -> None:
        for error in (OSError("diagnostic unavailable"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                def unavailable(event):
                    raise error

                drawing = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run",
                                                     operation_observer=unavailable)
                self.assertEqual(svg_objects(drawing.svg), ("far", "skin", "wall"))
                self.assertEqual(read_model_axis_elevation(self.repository, drawing.receipt_ref), drawing)
                with self.assertRaisesRegex(DrawingElevationError, "sha256"):
                    freeze_model_axis_elevation(self.repository, source=replace(self.source, step_sha256="f" * 64),
                                                view=_view(), drawing_run_id="refused-run", operation_observer=unavailable)
        observations = []
        with self.assertRaisesRegex(DrawingElevationError, "sha256"):
            freeze_model_axis_elevation(self.repository, source=replace(self.source, step_sha256="f" * 64),
                                        view=_view(), drawing_run_id="refused-run", operation_observer=observations.append)
        self.assertEqual([(row["phase"], row["status"]) for row in observations],
                         [("drawing.load", "running"), ("drawing.load", "failed")])
        self.assertFalse((self.root / "runs" / "refused-run").exists())

    def test_failed_or_cancelled_projection_records_no_render_or_write_that_did_not_run(self) -> None:
        for error in (occt_backend.OcctBackendError("projection stopped"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                observations = []
                with patch.object(drawing_elevation, "project_occt_lines", side_effect=error) as project:
                    with self.assertRaises(asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else DrawingElevationError):
                        freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="failed-run",
                                                    operation_observer=observations.append)
                self.assertEqual(project.call_count, 1)
                self.assertEqual(observations[-1]["phase"], "drawing.hlr")
                self.assertEqual(observations[-1]["status"], "cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                self.assertEqual({row["phase"] for row in observations}, {"drawing.load", "drawing.hlr"})
                self.assertFalse((self.root / "runs" / "failed-run").exists())

    def test_a_hidden_line_view_is_a_second_drawing_in_the_same_run(self) -> None:
        first = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run")
        second = freeze_model_axis_elevation(
            self.repository, source=self.source, view=_view(name="model-minus-y-elevation-hidden", hidden_lines=True),
            drawing_run_id="drawing-run",
        )
        self.assertEqual(svg_objects(second.svg), ("far", "rear", "skin", "wall"))
        self.assertIs(second.receipt["view"]["hidden_lines"], True)
        # A box's back edges lie under its front edges: drawn once, as visible. Undrawn hidden lines are not cleaned.
        self.assertGreater(second.cleanup["duplicate"], 0)
        self.assertEqual(first.cleanup["duplicate"], 0)
        visible = [segment for _, points in _group_lines(second.svg, "visible") for segment in zip(points, points[1:])]
        for object_id, points in _group_lines(second.svg, "hidden"):
            self.assertFalse(_lies_on(points, visible, drawing_elevation.CLEANUP_TOLERANCE_MM * 100 / 1000), object_id)
        self.assertEqual(len(list_model_axis_elevations(self.repository, "drawing-run")), 2)
        self.assertNotEqual(first.svg_ref.relative_path, second.svg_ref.relative_path)

    def test_a_source_that_does_not_verify_is_refused_before_anything_is_written(self) -> None:
        other_base = self.repository.put_json(
            run=self.source_run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
            record_kind=SEAT_OCCT_EXECUTION,
            payload=self.source_receipt(self.step_sha, identity={
                **self.source_receipt(self.step_sha)["identity"],
                "binding": {"project_id": PROJECT_ID, "run_id": SOURCE_RUN, "base": {**self.source_run.base.to_dict(), "version": 3},
                            "program_digest": "1" * 64, "stage_id": STAGE},
            }),
        )
        failed = self.repository.put_json(
            run=self.source_run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
            record_kind=SEAT_OCCT_EXECUTION, payload=self.source_receipt(self.step_sha, status="failed"),
        )
        missing_object = self.repository.put_json(
            run=self.source_run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
            record_kind=SEAT_OCCT_EXECUTION,
            payload=self.source_receipt(self.step_sha, physical_object_ids=sorted(OBJECTS) + ["ghost"],
                                        exact_artifact={"relative_path": STEP_NAME, "sha256": self.step_sha, "exact_brep": True,
                                                        "deliveries": {name: "closed_solid" for name in OBJECTS + ("ghost",)}}),
        )
        wrong = "f" * 64
        for label, source in (
            ("step sha", replace(self.source, step_sha256=wrong)),
            ("receipt sha", replace(self.source, cad_receipt_sha256=wrong)),
            ("receipt base", replace(self.source, cad_receipt_relative_path=other_base.relative_path, cad_receipt_sha256=other_base.sha256)),
            ("failed execution", replace(self.source, cad_receipt_relative_path=failed.relative_path, cad_receipt_sha256=failed.sha256)),
            ("object ids", replace(self.source, cad_receipt_relative_path=missing_object.relative_path, cad_receipt_sha256=missing_object.sha256)),
            ("unknown run", replace(self.source, run_id="nowhere", step_relative_path=self.source.step_relative_path.replace(SOURCE_RUN, "nowhere"),
                                    cad_receipt_relative_path=self.source.cad_receipt_relative_path.replace(SOURCE_RUN, "nowhere"))),
        ):
            with self.subTest(label=label), self.assertRaises(DrawingElevationError):
                freeze_model_axis_elevation(self.repository, source=source, view=_view(), drawing_run_id="drawing-run")
            self.assertFalse((self.root / "runs" / "drawing-run").exists(), label)
        with self.assertRaises(DrawingElevationError):
            freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id=SOURCE_RUN)
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(list_model_axis_elevations(self.repository, SOURCE_RUN), ())

    def test_a_retained_drawing_whose_bytes_changed_is_refused_on_read(self) -> None:
        drawing = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run")
        path = self.repository.layout.resolve_record(drawing.svg_ref)
        path.write_bytes(drawing.svg + b"<!-- tampered -->")
        with self.assertRaises(DrawingElevationError):
            read_model_axis_elevation(FilesystemProjectRepository.open(self.root), drawing.receipt_ref)


def _room_plan_source(root, *, hidden_witnesses=False, user_text=None):
    """A retained exact room with a real door cut; also used for visual inspection.

    ``user_text`` gives objects the ``archflow:`` user text a CAD program writes (component, material).
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    box = lambda origin, size: BRepPrimAPI_MakeBox(gp_Pnt(*origin), *size).Shape()
    shapes = {
        "south-wall": BRepAlgoAPI_Cut(box((0, 0, 0), (4, .2, 3)), box((1, -.1, 0), (1, .4, 2.1))).Shape(),
        "north-wall": box((0, 2.8, 0), (4, .2, 3)),
        "west-wall": box((0, .2, 0), (.2, 2.6, 3)),
        "east-wall": box((3.8, .2, 0), (.2, 2.6, 3)),
        "roof": box((0, 0, 3), (4, 3, .2)),
        "floor": box((0, 0, -.2), (4, 3, .2)),
    }
    witnesses = {}
    if hidden_witnesses:
        witnesses = {
            "door-inspection-witness": box((1, 0, 0), (1, .2, 2.1)),
            "inspection-screen": box((-.5, -.5, 1), (5, 4, .1)),
        }
        shapes.update(witnesses)
    repository = FilesystemProjectRepository.initialize(root, project_id=PROJECT_ID,
                                                        initial_state={"phase": "request", "commitments": []})
    run = repository.create_run(SOURCE_RUN)
    workspace = repository.layout.run(SOURCE_RUN).workspaces / WORKSPACE
    workspace.mkdir()
    step = workspace / STEP_NAME
    occt_backend.write_step(step, tuple(occt_backend.StepObject(name, shape, "room") for name, shape in shapes.items()),
                            length_unit="meter")
    digest = hashlib.sha256(step.read_bytes()).hexdigest()
    receipt = repository.put_json(
        run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
        record_kind=SEAT_OCCT_EXECUTION, payload={
            "schema": "OcctExecutionReceipt@1", "status": "succeeded", "readback_verified": True,
            "identity": {"schema": "OcctCadExportIdentity@1", "length_unit": "meter", "up_axis": "Z-up",
                         "binding": {"project_id": PROJECT_ID, "run_id": SOURCE_RUN, "base": run.base.to_dict(),
                                     "program_digest": "1" * 64, "stage_id": STAGE}},
            "exact_artifact": {"relative_path": STEP_NAME, "sha256": digest, "exact_brep": True,
                               "deliveries": {name: "closed_solid" for name in shapes}},
            "physical_object_ids": sorted(shapes),
            "expected_semantics": {"objects": {
                name: {"visible": name not in witnesses, **({"user_text": user_text[name]} if name in (user_text or {}) else {})}
                for name in shapes}},
        },
    )
    source = ElevationSource(SOURCE_RUN, f"runs/{SOURCE_RUN}/workspaces/{WORKSPACE}/{STEP_NAME}", digest,
                             receipt.relative_path, receipt.sha256)
    view = ElevationView(name="ground-plan", origin=(0, 0, 1.2), look=(0, 0, -1), right=(1, 0, 0), up=(0, 1, 0),
                         crop_uv=(-1, -1, 5, 4), near_depth=0, far_depth=1.3, scale_denominator=50)
    recipe = {"kind": "cut-plan", "name": "ground-plan", "frame": view.to_dict(),
              "graphics": {"cutLineMm": .35, "visibleLineMm": .18, "hatchSpacingMm": 2},
              "dimensions": [{"id": "door-width", "openingId": "door", "parameterRef": "door.width", "offsetMm": -8}]}
    dimension = {"id": "door-width", "status": "resolved", "start": [1, 0], "end": [2, 0],
                 "value": 1, "label": "1000 mm", "offsetMm": -8}
    return repository, source, recipe, dimension


@NEEDS_OCCT
class CutPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository, self.source, self.recipe, self.dimension = _room_plan_source(self.root)
        self.head = self.repository.read_head()

    def test_real_door_cut_roof_removal_dimensions_and_cold_readback_share_source_and_recipe(self):
        drawing = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe,
                                  drawing_run_id="plan-run", dimensions=(self.dimension,))
        self.assertEqual(drawing.receipt["schema"], "DrawingProjectionReceipt@1")
        self.assertEqual(drawing.receipt["view"], self.recipe)
        self.assertEqual(drawing.receipt["projection"]["dimensions"], [self.dimension])
        self.assertEqual(drawing.receipt["source"]["step"]["sha256"], self.source.step_sha256)
        self.assertEqual(drawing.receipt["projection"]["section_regions"], 4)
        self.assertNotIn("roof", svg_objects(drawing.svg), "the uncut top projection would hide the entire room behind its roof")
        self.assertIn("roof", drawing.receipt["source"]["physical_object_ids"])
        root = ElementTree.fromstring(drawing.svg)
        namespace = "{http://www.w3.org/2000/svg}"
        south = root.findall(f"{namespace}g[@id='section']/{namespace}polyline[@data-object='south-wall']")
        self.assertTrue(south)
        for line in south:
            xs = [float(pair.split(",")[0]) - 1 for pair in line.get("points").split()]
            self.assertFalse(min(xs) < 1.5 < max(xs), "the door opening must not be bridged by a cut line")
        self.assertIn(b"1000 mm", drawing.svg)
        self.assertEqual(drawing.run.base, self.repository.load_run(SOURCE_RUN).base)
        self.assertEqual(self.repository.read_head(), self.head)
        # The cut edge is drawn once, by the section: no visible line lies on it within 0.05 mm on the sheet.
        tolerance = drawing_elevation.CLEANUP_TOLERANCE_MM * 50 / 1000
        section = [segment for _, points in _group_lines(drawing.svg, "section") for segment in zip(points, points[1:])]
        for object_id, points in _group_lines(drawing.svg, "visible"):
            self.assertFalse(_lies_on(points, section, tolerance), (object_id, points))
        cleanup = drawing.receipt["cleanup"]
        self.assertEqual(drawing.cleanup, cleanup)
        self.assertEqual(cleanup["tolerance"], tolerance)
        self.assertGreater(cleanup["cut_precedence"], 0)
        self.assertEqual(cleanup["input_lines"] - cleanup["output_lines"],
                         sum(cleanup[rule] for rule in ("micro", "collinear", "cut_precedence", "duplicate", "hidden_under_cut")))
        self.assertNotIn("cleanup", drawing.receipt["view"], "the cleanup describes the drawing, not its recipe")
        reopened = FilesystemProjectRepository.open(self.root)
        cold = read_model_axis_elevation(reopened, drawing.receipt_ref)
        self.assertEqual((drawing.receipt, drawing.svg, drawing.png), (cold.receipt, cold.svg, cold.png))
        repeated = freeze_cut_plan(reopened, source=self.source, recipe=self.recipe,
                                   drawing_run_id="plan-run", dimensions=(self.dimension,))
        self.assertEqual(repeated.receipt_ref, drawing.receipt_ref)

    def test_the_cleaned_plan_draws_the_cut_once_and_only_the_floor_seen_through_the_door(self):
        drawing = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe,
                                  drawing_run_id="plan-run", dimensions=(self.dimension,))
        solved = drawing.receipt["projection"]["visible_polylines"]
        visible = _group_lines(drawing.svg, "visible")
        # Every wall's clipped top edge lay on its own cut; only the floor edge in the door opening is beyond it.
        self.assertLess(len(visible), solved)
        self.assertEqual(drawing.receipt["cleanup"]["cut_precedence"], solved - len(visible))
        self.assertEqual({object_id for object_id, _ in visible}, {"floor"})
        for _, points in visible:
            # SVG u = x + 1 and y = 3 - v in the crop (-1, -1, 5, 4): the door spans x 1..2 at y = 0.
            self.assertTrue(all(2 - 1e-4 <= u <= 3 + 1e-4 and abs(v - 4) <= 1e-4 for u, v in points), points)
        # The cut itself is drawn as solved.
        self.assertEqual(drawing.receipt["projection"]["section_polylines"], len(_group_lines(drawing.svg, "section")))
        # The PNG is rendered from these SVG bytes and nothing else.
        self.assertEqual(drawing.png, render_svg_png(drawing.svg))

    def test_a_receipt_retained_before_cleanup_still_reads_cold(self):
        drawing = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe,
                                  drawing_run_id="plan-run", dimensions=(self.dimension,))
        earlier = {key: value for key, value in drawing.receipt.items() if key != "cleanup"}
        ref = self.repository.put_json(
            run=drawing.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=drawing.run.run_id),
            record_kind=DRAWING_PROJECTION_RECEIPT, payload=earlier)
        cold = read_model_axis_elevation(FilesystemProjectRepository.open(self.root), ref)
        self.assertIsNone(cold.cleanup)
        self.assertEqual((cold.receipt, cold.svg, cold.png), (earlier, drawing.svg, drawing.png))

    def test_the_plan_names_components_and_materials_and_draws_their_hatch_poche_and_fade(self):
        from PIL import Image

        text = {name: {"archflow:component": name, "archflow:material": "brick", "archflow:producer_op": "wall"}
                for name in ("north-wall", "west-wall", "east-wall")}
        text["south-wall"] = {"archflow:component": "south-wall", "archflow:material": "concrete"}
        text["floor"] = {"archflow:component": "ground-slab"}
        repository, source, recipe, dimension = _room_plan_source(self.root.parent / "semantic-room", user_text=text)
        recipe = {**recipe, "graphics": {**recipe["graphics"], "hatch": {"byMaterial": {"concrete": {"poche": True}}},
                                         "beyond": {"fade": 0.5}}}
        drawing = freeze_cut_plan(repository, source=source, recipe=recipe, drawing_run_id="plan-run", dimensions=(dimension,))
        self.assertEqual(drawing.receipt["view"], recipe, "graphics rules are representation intent, retained as given")
        root = ElementTree.fromstring(drawing.svg)
        named = [element for element in root.iter() if element.get("data-object") is not None]
        self.assertTrue(named)
        for element in named:
            row = text.get(element.get("data-object"), {})
            self.assertEqual((element.get("data-component"), element.get("data-material")),
                             (row.get("archflow:component"), row.get("archflow:material")), element.attrib)
        # Concrete is filled: one even-odd polygon for the south wall's two pieces either side of the door;
        # brick keeps the default hatch strokes; nothing below the cut is either.
        material = root.find(f"{SVG_NS}g[@id='section-hatch']")
        self.assertEqual([(p.get("data-object"), p.get("fill-rule")) for p in material.findall(f"{SVG_NS}polygon")],
                         [("south-wall", "evenodd")])
        self.assertEqual({line.get("data-object") for line in material.findall(f"{SVG_NS}polyline")},
                         {"north-wall", "west-wall", "east-wall"})
        self.assertEqual(root.find(f"{SVG_NS}g[@id='visible']").get("stroke"), "#808080")
        self.assertEqual(root.find(f"{SVG_NS}g[@id='section']").get("stroke"), "#000")
        self.assertEqual(drawing.png, render_svg_png(drawing.svg))
        with Image.open(BytesIO(render_svg_png(drawing.svg, dots_per_inch=254))) as image:
            # 1:50 at 254 dpi: 200 pixels per metre, from the crop's corner (-1, 4).
            pixel = lambda x, y: image.getpixel((round((x + 1) * 200), round((4 - y) * 200)))  # noqa: E731
            self.assertEqual(pixel(0.5, 0.1), 0, "the concrete south wall is poché")
            self.assertEqual(pixel(3.0, 0.1), 0)
            self.assertEqual(pixel(1.5, 0.1), 255, "the door between its two pieces stays open")
            self.assertEqual(pixel(2.0, 1.5), 255, "the room is empty")
            # Inside the north wall (x 0.5..3.5, y 2.8..3.0), clear of its cut outline.
            north = image.crop((300, 205, 900, 235))
            self.assertLess(north.getextrema()[0], 128, "the brick north wall keeps its hatch")
            self.assertGreater(north.getextrema()[1], 128)

    def test_revision_preserves_recipe_intent_and_explicit_missing_dimension_without_a_number(self):
        first = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe,
                                drawing_run_id="plan-run", dimensions=(self.dimension,))
        missing = {"id": "door-width", "status": "missing", "detail": "The source opening was removed.", "offsetMm": -8}
        second = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe, drawing_run_id="plan-rebuild",
                                 dimensions=(missing,), previous_revision_ref=first.receipt_ref.uri)
        self.assertEqual(second.receipt["previousRevisionRef"], first.receipt_ref.uri)
        self.assertEqual(second.receipt["view"]["dimensions"], self.recipe["dimensions"])
        self.assertEqual(second.receipt["projection"]["dimensions"], [missing])
        self.assertNotIn(b"1000 mm", second.svg)
        self.assertNotIn(b"<text ", second.svg)
        self.assertEqual(read_model_axis_elevation(self.repository, first.receipt_ref).svg, first.svg)

    def test_hidden_source_witnesses_neither_fill_the_door_nor_occlude_background_geometry(self):
        baseline = freeze_cut_plan(self.repository, source=self.source, recipe=self.recipe,
                                   drawing_run_id="plan-run", dimensions=(self.dimension,))
        repository, source, recipe, dimension = _room_plan_source(self.root.parent / "witness-source", hidden_witnesses=True)
        drawing = freeze_cut_plan(repository, source=source, recipe=recipe,
                                  drawing_run_id="plan-run", dimensions=(dimension,))
        source_ids = drawing.receipt["source"]["physical_object_ids"]
        selected_ids = drawing.receipt["projection"]["selected_object_ids"]
        for name in ("door-inspection-witness", "inspection-screen"):
            self.assertIn(name, source_ids, "the exact STEP still retains its source evidence")
            self.assertNotIn(name, selected_ids)
            self.assertNotIn(name, svg_objects(drawing.svg))
        self.assertEqual(drawing.receipt["projection"]["section_regions"], 4)
        self.assertEqual(drawing.svg, baseline.svg, "an invisible aperture cannot add cut fill and an invisible screen cannot hide the floor")
        self.assertEqual(drawing.png, baseline.png)

    def test_bad_source_frame_or_graphics_refuses_before_a_drawing_run_is_created(self):
        for recipe in (
            {**self.recipe, "frame": {**self.recipe["frame"], "look": [0, 1, 0]}},
            {**self.recipe, "graphics": {**self.recipe["graphics"], "cutLineMm": 0}},
            {**self.recipe, "hiddenObjectIds": "not-a-list"},
        ):
            with self.subTest(recipe=recipe), self.assertRaises(DrawingElevationError):
                freeze_cut_plan(self.repository, source=self.source, recipe=recipe, drawing_run_id="bad-plan")
        with self.assertRaises(DrawingElevationError):
            freeze_cut_plan(self.repository, source=replace(self.source, step_sha256="f" * 64),
                            recipe=self.recipe, drawing_run_id="bad-plan")
        self.assertFalse(self.repository.layout.run("bad-plan").manifest.exists())
        self.assertEqual(self.repository.read_head(), self.head)


    def test_rebuild_preserves_deleted_hidden_object_intent_and_renders_remaining_source(self):
        recipe = {**self.recipe, "hiddenObjectIds": ["deleted-partition", "east-wall"]}
        drawing = freeze_cut_plan(self.repository, source=self.source, recipe=recipe,
                                  drawing_run_id="plan-rebuild", dimensions=(self.dimension,))
        self.assertEqual(drawing.receipt["view"], recipe)
        self.assertEqual(drawing.receipt["view"]["hiddenObjectIds"], ["deleted-partition", "east-wall"])
        self.assertEqual(drawing.receipt["projection"]["unresolvedObjectIds"], ["deleted-partition"])
        self.assertNotIn("east-wall", drawing.receipt["projection"]["selected_object_ids"])
        self.assertNotIn("east-wall", svg_objects(drawing.svg))
        self.assertIn("south-wall", svg_objects(drawing.svg))
        cold = read_model_axis_elevation(FilesystemProjectRepository.open(self.root), drawing.receipt_ref)
        self.assertEqual((cold.svg, cold.png, cold.receipt), (drawing.svg, drawing.png, drawing.receipt))
        self.assertEqual(self.repository.read_head(), self.head)


if __name__ == "__main__":
    unittest.main()
