"""One model-axis elevation frozen from a retained exact STEP through P036, and read back cold."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from archflow.adapters import occt_backend
from monkeydiagram.drawing_svg import svg_objects
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DRAWING_PROJECTION_RECEIPT, SEAT_OCCT_EXECUTION
from archflow.project.repository import FilesystemProjectRepository
from monkeydiagram.drawing_elevation import (
    DrawingElevationError,
    ElevationSource,
    ElevationView,
    freeze_model_axis_elevation,
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
        drawing = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run")
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

        again = freeze_model_axis_elevation(reopened, source=self.source, view=_view(), drawing_run_id="drawing-run")
        self.assertEqual((again.receipt_ref, again.svg_ref, again.png_ref), (drawing.receipt_ref, drawing.svg_ref, drawing.png_ref))
        self.assertEqual(len(list_model_axis_elevations(reopened, "drawing-run")), 1)

    def test_a_hidden_line_view_is_a_second_drawing_in_the_same_run(self) -> None:
        first = freeze_model_axis_elevation(self.repository, source=self.source, view=_view(), drawing_run_id="drawing-run")
        second = freeze_model_axis_elevation(
            self.repository, source=self.source, view=_view(name="model-minus-y-elevation-hidden", hidden_lines=True),
            drawing_run_id="drawing-run",
        )
        self.assertEqual(svg_objects(second.svg), ("far", "rear", "skin", "wall"))
        self.assertIs(second.receipt["view"]["hidden_lines"], True)
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


if __name__ == "__main__":
    unittest.main()
