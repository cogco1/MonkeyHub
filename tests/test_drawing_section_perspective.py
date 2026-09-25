"""A true section perspective: an exact cut, an exact perspective, poché, one retention boundary, named refusals.

The Studio route over the same pipeline is tested in apps/archflow-studio/api/tests/test_section_perspective.py.
"""

from __future__ import annotations

import hashlib
import base64
import math
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from io import BytesIO

from archflow.adapters import occt_backend
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SEAT_OCCT_EXECUTION, STUDIO_MODEL_ASSET
from archflow.project.repository import FilesystemProjectRepository
from monkeydiagram.drawing_elevation import (
    SECTION_PERSPECTIVE_KIND,
    ElevationSource,
    NativeModelSource,
    SectionPerspectiveError,
    SectionPerspectiveView,
    freeze_section_perspective,
    list_model_axis_elevations,
    object_semantics,
    project_section_perspective,
    read_elevation_source,
    read_model_axis_elevation,
)
from monkeydiagram.drawing_svg import svg_objects

NEEDS_OCCT = unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
SOURCE_RUN = "source-run"
STEP_NAME = "room@abc123.step"
WORKSPACE = "cad-room"
# The room cut through the middle: y = 4 keeps the north half; the eye stands to the south.
SECTION = {"line": [[0.0, 4.0], [6.0, 4.0]], "keep": "left"}
FOCUS = 3.3 / math.tan(math.radians(27.5))  # half the cut's width plus its 5% margin, in a 55 degree field
CUT_BOXES = {"floor": (0.0, -0.3, 6.0, 0.0), "roof": (0.0, 3.0, 6.0, 3.3),
             "wall-west": (0.0, 0.0, 0.3, 3.0), "wall-east": (5.7, 0.0, 6.0, 3.0)}
PILLAR = (4.5, 6.5, 0.25)


def _room() -> dict[str, object]:
    """Floor, roof slab, four walls with a door in the far wall, a column, a pillar, a table on the removed side
    and the door's hidden aperture witness, in CAD Z-up metres."""

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    def box(low, high):
        return BRepPrimAPI_MakeBox(gp_Pnt(*low), gp_Pnt(*high)).Shape()

    door = box((2.5, 7.6, 0.0), (3.5, 8.1, 2.1))
    return {
        "floor": box((0, 0, -0.3), (6, 8, 0)),
        "roof": box((0, 0, 3), (6, 8, 3.3)),
        "wall-north": BRepAlgoAPI_Cut(box((0, 7.7, 0), (6, 8, 3)), door).Shape(),
        "wall-south": box((0, 0, 0), (6, 0.3, 3)),
        "wall-west": box((0, 0.3, 0), (0.3, 7.7, 3)),
        "wall-east": box((5.7, 0.3, 0), (6, 7.7, 3)),
        "column": box((1, 6, 0), (1.3, 6.3, 3)),
        "pillar": BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(PILLAR[0], PILLAR[1], 0), gp_Dir(0, 0, 1)), PILLAR[2], 3).Shape(),
        "table": box((2, 1, 0), (3, 2, 0.8)),
        "door-aperture": box((2.5, 7.7, 0.0), (3.5, 8.0, 2.1)),
    }


OBJECTS = ("column", "door-aperture", "floor", "pillar", "roof", "table",
           "wall-east", "wall-north", "wall-south", "wall-west")
DRAWN = tuple(name for name in OBJECTS if name != "door-aperture")


def _image(point, eye, focus=FOCUS):
    """An independent statement of the perspective: the plane y = 4 maps 1:1, depth shrinks toward the eye's foot."""

    x, y, z = point
    shrink = 1.0 + (y - 4.0) / focus
    return eye[0] + (x - eye[0]) / shrink, eye[2] + (z - eye[2]) / shrink


def _segments(lines, *, exclude=()):
    for line in lines:
        if line.kind == "visible" and line.object_id not in exclude:
            yield from ((line.object_id, a, b) for a, b in zip(line.points, line.points[1:]))


def _through(a, b, point, tolerance=1e-9) -> bool:
    cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    return abs(cross) <= tolerance * max(1.0, math.dist(a, b) * math.dist(a, point))


def _vanishing_point(lines, starts):
    """Intersect the receding room corners that start at the cut: where they meet is the vanishing point."""

    rays = []
    for start in starts:
        found = [(a, b) for _, a, b in _segments(lines) if math.dist(a, start) <= 1e-9 or math.dist(b, start) <= 1e-9]
        found = [(a, b) if math.dist(a, start) <= 1e-9 else (b, a) for a, b in found]
        receding = [pair for pair in found if abs(pair[1][0] - pair[0][0]) > 1e-6 and abs(pair[1][1] - pair[0][1]) > 1e-6]
        assert len(receding) == 1, (start, found)
        rays.append(receding[0])
    (a, b), (c, d) = rays[:2]
    denominator = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / denominator
    return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])


@NEEDS_OCCT
class ProjectorConventionTests(unittest.TestCase):
    """What OCCT's perspective projector does, measured before anything is built on it."""

    def test_the_projection_plane_maps_one_to_one_and_depth_shrinks_toward_the_principal_point(self) -> None:
        from OCP.HLRAlgo import HLRAlgo_Projector
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Pnt2d

        # P = (1, 2, 3); N = +Y (toward the eye); X = +X, so the picture's Y is N x X = -Z.
        projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(1, 2, 3), gp_Dir(0, 1, 0), gp_Dir(1, 0, 0)), 10.0)

        def project(x, y, z):
            image = gp_Pnt2d()
            projector.Project(gp_Pnt(x, y, z), image)
            return image.X(), image.Y()

        self.assertTrue(projector.Perspective())
        for point, expected in (((1, 2, 3), (0, 0)), ((2, 2, 3), (1, 0)), ((1, 2, 4), (0, -1)), ((3, 2, 5), (2, -2))):
            for actual, stated in zip(project(*point), expected):
                self.assertAlmostEqual(actual, stated, places=12, msg=f"{point} lies on the projection plane")
        for depth in (2.0, 10.0, 100.0):
            self.assertAlmostEqual(project(2, 2 - depth, 3)[0], 1.0 / (1.0 + depth / 10.0), places=12,
                                   msg="a point `depth` behind the plane maps 1 / (1 + depth / focus) of the way")
        self.assertAlmostEqual(project(2, 2 + 9.9, 3)[0], 100.0, places=6, msg="the eye stands at P + focus * N")
        self.assertLess(math.hypot(*project(5, -1e6, 7)), 1e-4, msg="lines along N converge at P")


@NEEDS_OCCT
class SectionPerspectiveGeometryTests(unittest.TestCase):
    """The exact cut and perspective of the fixture room, in memory."""

    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        path = Path(temporary.name) / STEP_NAME
        occt_backend.write_step(path, tuple(occt_backend.StepObject(name, shape, "layer") for name, shape in _room().items()),
                                length_unit="meter")
        cls.entries = occt_backend.read_step(path, length_unit="meter")
        cls.default = cls.project()
        cls.moved = cls.project(camera={"eye": [4.5, 4.0 - FOCUS, 1.3], "target": [3.0, 4.0, 1.5]})

    @classmethod
    def project(cls, **changes):
        view = SectionPerspectiveView(name="room-section", section=deepcopy(SECTION), scale_denominator=25, **changes)
        return project_section_perspective(cls.entries, object_ids=DRAWN, view=view, unit="meter")

    def test_the_cut_is_filled_true_to_scale_where_the_plane_meets_walls_floor_and_roof(self) -> None:
        perspective = self.default.perspective
        self.assertEqual(sorted(region.object_id for region in perspective.regions), sorted(CUT_BOXES))
        for region in perspective.regions:
            points = [point for loop in region.loops for point in loop]
            bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
            for actual, expected in zip(bounds, CUT_BOXES[region.object_id]):
                self.assertAlmostEqual(actual, expected, places=9, msg=region.object_id)
        self.assertEqual(perspective.cut_object_ids, tuple(sorted(CUT_BOXES)))
        # The picture plane is the cut: projecting the loops changed nothing, and moving the eye moves no cut.
        orthographic = occt_backend.section_occt_regions(self.entries, object_ids=DRAWN, origin=(0, 4, 0), right=(1, 0, 0),
                                                         up=(0, 0, 1), linear_deflection=0.0001)
        for pair in ((perspective.regions, orthographic), (perspective.regions, self.moved.perspective.regions)):
            for left, right in zip(*pair):
                self.assertEqual(left.object_id, right.object_id)
                for a, b in zip((p for loop in left.loops for p in loop), (p for loop in right.loops for p in loop)):
                    self.assertAlmostEqual(math.dist(a, b), 0.0, places=9)
        svg = self.default.svg.decode()
        self.assertIn('<g id="section-hatch"', svg)
        self.assertIn('data-projection="section-perspective" data-scale-at="section-plane"', svg)

    def test_nothing_from_the_removed_side_is_drawn(self) -> None:
        perspective = self.default.perspective
        self.assertEqual(perspective.removed_object_ids, ("table", "wall-south"))
        self.assertTrue({"table", "wall-south"}.isdisjoint(svg_objects(self.default.svg)))
        self.assertTrue({"table", "wall-south"}.isdisjoint(line.object_id for line in perspective.lines))
        # Everything kept lies behind the cut inside the room, so its image stays within the cut's outline;
        # anything between the cut and the eye would be magnified past it.
        for line in perspective.lines:
            for u, v in line.points:
                self.assertTrue(-1e-9 <= u <= 6 + 1e-9 and -0.3 - 1e-9 <= v <= 3.3 + 1e-9, (line.object_id, u, v))

    def test_edges_along_the_view_axis_converge_at_the_principal_point(self) -> None:
        perspective = self.default.perspective
        principal = perspective.principal_point
        self.assertAlmostEqual(principal[0], 3.0, places=9)
        self.assertAlmostEqual(principal[1], 1.3, places=9)
        self.assertAlmostEqual(perspective.focus, FOCUS, places=9)
        receding = 0
        for name, a, b in _segments(perspective.lines, exclude={"pillar"}):
            horizontal, vertical = abs(a[1] - b[1]) <= 1e-9, abs(a[0] - b[0]) <= 1e-9
            if not (horizontal or vertical):
                self.assertTrue(_through(a, b, principal), f"{name} {a} {b} is neither parallel to the cut nor receding")
                receding += 1
        self.assertGreaterEqual(receding, 8)
        corners = ((0.3, 0.0), (5.7, 0.0), (0.3, 3.0), (5.7, 3.0))
        for actual, expected in zip(_vanishing_point(perspective.lines, corners), principal):
            self.assertAlmostEqual(actual, expected, places=9)
        # The far wall's corners land where the independent statement of the perspective puts them.
        eye = (3.0, 4.0 - FOCUS, 1.3)
        far = [_image(point, eye) for point in ((0.3, 7.7, 0.0), (5.7, 7.7, 3.0))]
        points = {point for line in perspective.lines for point in line.points}
        for expected in far:
            self.assertTrue(any(math.dist(point, expected) <= 1e-9 for point in points), expected)

    def test_moving_the_eye_sideways_moves_the_vanishing_point_and_not_the_cut(self) -> None:
        corners = ((0.3, 0.0), (5.7, 0.0), (0.3, 3.0), (5.7, 3.0))
        before = _vanishing_point(self.default.perspective.lines, corners)
        after = _vanishing_point(self.moved.perspective.lines, corners)
        self.assertAlmostEqual(after[0] - before[0], 1.5, places=9)
        self.assertAlmostEqual(after[1] - before[1], 0.0, places=9)
        self.assertEqual(self.moved.perspective.principal_point, (4.5, 1.3))
        self.assertNotEqual(self.default.svg, self.moved.svg)
        self.assertEqual(self.moved.view["camera"]["placement"], "explicit")
        # Same target, same distance, same field of view: the frame stays on the cut.
        for moved, default in zip(self.moved.view["crop_uv"], self.default.view["crop_uv"]):
            self.assertAlmostEqual(moved, default, places=9)

    def test_the_target_centres_a_frame_as_wide_as_the_field_of_view(self) -> None:
        eye = (3.0, 4.0 - FOCUS, 1.3)
        door = (3.0, 7.7, 1.05)
        zoomed = self.project(camera={"eye": list(eye), "target": list(door), "fovDeg": 30})
        centre = _image(door, eye)
        crop = zoomed.view["crop_uv"]
        self.assertAlmostEqual((crop[0] + crop[2]) / 2, centre[0], places=9)
        self.assertAlmostEqual((crop[1] + crop[3]) / 2, centre[1], places=9)
        self.assertAlmostEqual(crop[2] - crop[0], 2 * FOCUS * math.tan(math.radians(15)), places=9)
        self.assertAlmostEqual((crop[3] - crop[1]) / (crop[2] - crop[0]), 4.2 / 6.6, places=9, msg="the cut's proportions")
        self.assertEqual(zoomed.perspective.principal_point, self.default.perspective.principal_point)

    def test_hidden_back_faces_and_curved_silhouettes_are_exact(self) -> None:
        """Regression for HLRBRep's perspective with a non-identity projector frame (see _perspective_camera)."""

        eye = (3.0, 4.0 - FOCUS, 1.3)
        corners = [_image(point, eye) for point in ((0, 4, 0), (6, 4, 0), (6, 8, 0), (0, 8, 0))]

        def inside(point):
            signs = [(b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
                     for a, b in zip(corners, corners[1:] + corners[:1])]
            return all(sign > 1e-6 for sign in signs) or all(sign < -1e-6 for sign in signs)

        for name, a, b in _segments(self.default.perspective.lines):
            if name == "floor":
                self.assertFalse(inside(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)), "an edge under the floor shows through it")
        # A vertical cylinder's silhouettes are where the eye's horizontal rays touch it.
        cx, cy, radius = PILLAR
        dx, dy = eye[0] - cx, eye[1] - cy
        base, half = math.atan2(dy, dx), math.acos(radius / math.hypot(dx, dy))
        expected = sorted(_image((cx + radius * math.cos(base + s * half), cy + radius * math.sin(base + s * half), 1.5), eye)[0]
                          for s in (1, -1))
        verticals = sorted({round(a[0], 9) for name, a, b in _segments(self.default.perspective.lines)
                            if name == "pillar" and abs(a[0] - b[0]) <= 1e-9 and abs(a[1] - b[1]) > 1.0})
        self.assertEqual(len(verticals), 2, verticals)
        for actual, stated in zip(verticals, expected):
            self.assertAlmostEqual(actual, stated, places=6)

    def test_the_same_request_gives_the_same_bytes(self) -> None:
        again = self.project()
        self.assertEqual((again.svg, again.png, again.view), (self.default.svg, self.default.png, self.default.view))
        # The recorded camera is exact: stating it explicitly draws the same picture.
        camera = self.default.view["camera"]
        explicit = self.project(camera={"eye": camera["eye"], "target": camera["target"], "fovDeg": camera["fov_deg"]})
        self.assertEqual(explicit.svg, self.default.svg)


class SectionPerspectiveRequestTests(unittest.TestCase):
    """Every refusal is made before the model is read, and names the rule it broke."""

    def test_each_refusal_names_its_rule(self) -> None:
        line = deepcopy(SECTION)
        for code, changes in (
            ("SECTION_LINE_DEGENERATE", dict(section={"line": [[1, 4], [1, 4]], "keep": "left"})),
            ("SECTION_NORMAL_DEGENERATE", dict(section={"origin": [0, 4, 0], "normal": [0, 0, 0]})),
            ("SECTION_VALUE_NOT_FINITE", dict(section={"line": [[0, 4], [6, float("nan")]], "keep": "left"})),
            ("SECTION_VALUE_NOT_FINITE", dict(section=line, camera={"eye": [3, float("inf"), 1], "target": [3, 4, 1]})),
            ("SECTION_VALUE_NOT_FINITE", dict(section=line, depth=float("nan"))),
            ("SECTION_EYE_ON_KEPT_SIDE", dict(section=line, camera={"eye": [3, 6, 1.5], "target": [3, 8, 1.5]})),
            ("SECTION_EYE_ON_PLANE", dict(section=line, camera={"eye": [3, 4, 1.5], "target": [3, 8, 1.5]})),
            ("SECTION_CAMERA_DEGENERATE", dict(section=line, camera={"eye": [3, 0, 1.5], "target": [3, 0, 1.5]})),
            ("SECTION_CAMERA_DEGENERATE", dict(section=line, camera={"eye": [3, 0, 1.5], "target": [3, -2, 1.5]})),
            ("SECTION_CAMERA_DEGENERATE", dict(section=line, camera={"fovDeg": 180})),
            ("SECTION_CAMERA_DEGENERATE", dict(section={"origin": [0, 0, 1], "normal": [0, 0, 1]})),
            ("SECTION_CAMERA_DEGENERATE", dict(section=line, camera={"up": [0, 2, 0]})),
            ("SECTION_DEPTH_INVALID", dict(section=line, depth=0)),
            ("SECTION_REQUEST_INVALID", dict(section={"line": [[0, 4], [6, 4]], "keep": "up"})),
            ("SECTION_REQUEST_INVALID", dict(section={"line": [[0, 4], [6, 4]], "origin": [0, 0, 0]})),
            ("SECTION_REQUEST_INVALID", dict(section=line, camera={"eye": [3, 0, 1.5]})),
            ("SECTION_REQUEST_INVALID", dict(section=line, camera={"eye": [3, 0, 1.5], "target": [3, 4, 1.5], "eyeHeight": 2})),
        ):
            with self.subTest(code=code, changes=changes), self.assertRaises(SectionPerspectiveError) as refused:
                SectionPerspectiveView(name="refused", **changes)
            self.assertEqual(refused.exception.code, code)

    def test_a_horizontal_section_needs_only_an_up_across_it(self) -> None:
        view = SectionPerspectiveView(name="plan-perspective", section={"origin": [0, 0, 1], "normal": [0, 0, 1]},
                                      camera={"up": [0, 1, 0]})
        origin, right, up, normal = view.frame()
        self.assertEqual((origin, right, up, normal), ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


@NEEDS_OCCT
class FreezeSectionPerspectiveTests(unittest.TestCase):
    """The one retention boundary: drawing run on the source base, exact plane and camera, cold read-back."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "section-project"
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id="section-project", initial_state={"phase": "request", "commitments": []},
        )
        self.source_run = self.repository.create_run(SOURCE_RUN)
        workspace = self.repository.layout.run(SOURCE_RUN).workspaces / WORKSPACE
        workspace.mkdir()
        occt_backend.write_step(workspace / STEP_NAME,
                                tuple(occt_backend.StepObject(name, shape, "layer") for name, shape in _room().items()),
                                length_unit="meter")
        self.step_sha = hashlib.sha256((workspace / STEP_NAME).read_bytes()).hexdigest()
        self.receipt_ref = self.repository.put_json(
            run=self.source_run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=SOURCE_RUN),
            record_kind=SEAT_OCCT_EXECUTION, payload={
                "schema": "OcctExecutionReceipt@1", "status": "succeeded", "readback_verified": True,
                "identity": {
                    "schema": "OcctCadExportIdentity@1", "length_unit": "meter", "up_axis": "Z-up",
                    "binding": {"project_id": "section-project", "run_id": SOURCE_RUN,
                                "base": self.source_run.base.to_dict(), "program_digest": "1" * 64, "stage_id": "room"},
                },
                "exact_artifact": {"relative_path": STEP_NAME, "sha256": self.step_sha, "exact_brep": True,
                                   "deliveries": {name: "closed_solid" for name in OBJECTS}},
                "physical_object_ids": sorted(OBJECTS),
                # The door's aperture volume is retained as hidden inspection evidence, not material.
                "expected_semantics": {"objects": {"door-aperture": {"visible": False}}},
            },
        )
        self.source = ElevationSource(
            run_id=SOURCE_RUN, step_relative_path=f"runs/{SOURCE_RUN}/workspaces/{WORKSPACE}/{STEP_NAME}",
            step_sha256=self.step_sha,
            cad_receipt_relative_path=self.receipt_ref.relative_path, cad_receipt_sha256=self.receipt_ref.sha256,
        )
        self.head = self.repository.read_head()

    def view(self, **changes) -> SectionPerspectiveView:
        return SectionPerspectiveView(**{"name": "room-section", "section": deepcopy(SECTION), "scale_denominator": 25, **changes})

    def test_the_receipt_records_source_plane_and_camera_exactly_and_reads_back_the_same_bytes(self) -> None:
        observations = []
        drawing = freeze_section_perspective(self.repository, source=self.source, view=self.view(), drawing_run_id="drawing-run",
                                             operation_observer=observations.append, parent_event_id="operation")
        self.assertEqual([event["phase"] for event in observations if event["status"] == "succeeded"],
                         ["drawing.load", "drawing.hlr", "drawing.svg", "drawing.png", "drawing.persist"])
        receipt = drawing.receipt
        self.assertEqual(receipt["source"]["step"], {"relative_path": self.source.step_relative_path, "sha256": self.step_sha,
                                                     "media_type": "model/step"})
        self.assertEqual(receipt["source"]["cad_receipt"], {"relative_path": self.receipt_ref.relative_path,
                                                            "sha256": self.receipt_ref.sha256})
        self.assertEqual((receipt["source"]["run_id"], receipt["source"]["base"]), (SOURCE_RUN, self.source_run.base.to_dict()))
        view = receipt["view"]
        self.assertEqual(view["kind"], SECTION_PERSPECTIVE_KIND)
        self.assertEqual(view["request"]["section"], SECTION)
        self.assertEqual(view["section"], {"origin": [0.0, 4.0, 0.0], "normal": [0.0, -1.0, 0.0],
                                           "kept_side": "dot(point - origin, normal) <= 0", "depth": None})
        self.assertEqual((view["origin"], view["right"], view["up"]), ([0.0, 4.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]))
        camera = view["camera"]
        self.assertEqual((camera["placement"], camera["up"], camera["fov_deg"]), ("default", [0.0, 0.0, 1.0], 55.0))
        for actual, expected in zip(camera["eye"] + camera["target"], [3.0, 4.0 - FOCUS, 1.3, 3.0, 4.0, 1.5]):
            self.assertAlmostEqual(actual, expected, places=12)
        for actual, expected in zip(view["crop_uv"], (-0.3, -0.6, 6.3, 3.6)):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual((view["scale"], view["scale_at"]), ("1:25", "the section plane"))
        projection = receipt["projection"]
        # The solved camera is the recorded one, to the last bit.
        self.assertEqual(projection["principal_point_uv"], [camera["eye"][0], camera["eye"][2]])
        self.assertEqual(projection["focus"], 4.0 - camera["eye"][1])
        self.assertNotIn("door-aperture", projection["selected_object_ids"])
        self.assertEqual(projection["removed_object_ids"], ["table", "wall-south"])
        self.assertEqual(projection["cut_object_ids"], sorted(CUT_BOXES))
        self.assertEqual(projection["hidden_polylines"], 0)
        # The kept parts' faces on the picture plane repeat the cut; it is drawn once, by the section,
        # at 0.05 mm on the sheet measured where the scale holds.
        self.assertEqual(receipt["cleanup"]["tolerance"], 0.05 * 25 / 1000)
        self.assertGreater(receipt["cleanup"]["cut_precedence"], 0)
        self.assertEqual(drawing.cleanup, receipt["cleanup"])
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(drawing.run.base, self.source_run.base)

        reopened = FilesystemProjectRepository.open(self.root)
        (receipt_ref,) = list_model_axis_elevations(reopened, "drawing-run")
        cold = read_model_axis_elevation(reopened, receipt_ref)
        self.assertEqual((cold.svg, cold.png, cold.receipt), (drawing.svg, drawing.png, receipt))
        self.assertEqual(receipt["artifacts"]["svg"]["sha256"], hashlib.sha256(cold.svg).hexdigest())
        self.assertEqual(receipt["artifacts"]["png"]["sha256"], hashlib.sha256(cold.png).hexdigest())
        # The receipt's request, on its verified source and that source's semantics, draws the retained files again.
        request = view["request"]
        verified = read_elevation_source(reopened, self.source)
        replay = project_section_perspective(
            verified.entries, object_ids=projection["selected_object_ids"], unit="meter",
            view=SectionPerspectiveView(name=request["name"], section=request["section"], camera=request["camera"],
                                        depth=request["depth"], hidden_object_ids=tuple(request["hiddenObjectIds"]),
                                        scale_denominator=int(request["scale"].split(":")[1]), graphics=request["graphics"],
                                        linear_deflection=request["linear_deflection"]),
            semantics=object_semantics(verified.receipt))
        self.assertEqual((replay.svg, replay.png), (cold.svg, cold.png))
        again = freeze_section_perspective(reopened, source=self.source, view=self.view(), drawing_run_id="drawing-run")
        self.assertEqual((again.receipt_ref, again.svg_ref, again.png_ref), (drawing.receipt_ref, drawing.svg_ref, drawing.png_ref))

    def test_hidden_objects_are_left_out_of_cut_and_view(self) -> None:
        drawing = freeze_section_perspective(self.repository, source=self.source, drawing_run_id="drawing-run",
                                             view=self.view(hidden_object_ids=("column", "wall-west")),
                                             attribution={"actorId": "local", "authenticated": False, "origin": "hub"},
                                             reason="Take the column out of the section")
        self.assertTrue({"column", "wall-west"}.isdisjoint(drawing.receipt["projection"]["selected_object_ids"]))
        self.assertTrue({"column", "wall-west"}.isdisjoint(svg_objects(drawing.svg)))
        self.assertEqual(drawing.receipt["projection"]["cut_object_ids"], ["floor", "roof", "wall-east"])
        self.assertEqual(drawing.receipt["view"]["hiddenObjectIds"], ["column", "wall-west"])
        # Who asked and why are kept with this revision only; they are not part of the view.
        self.assertEqual((drawing.attribution, drawing.reason),
                         ({"actorId": "local", "authenticated": False, "origin": "hub"}, "Take the column out of the section"))
        self.assertNotIn("attribution", drawing.receipt["view"])

    def test_refusals_after_reading_the_model_are_named_and_write_nothing(self) -> None:
        for code, view in (
            ("SECTION_PLANE_MISSES_MODEL", self.view(section={"line": [[0, 40], [6, 40]], "keep": "left"})),
            ("SECTION_PLANE_MISSES_MODEL", self.view(section={"origin": [0, 0, 9], "normal": [0, 0, 1]}, camera={"up": [0, 1, 0]})),
            ("DRAWING_OBJECT_UNKNOWN", self.view(hidden_object_ids=("ghost",))),
            ("DRAWING_EMPTY", self.view(hidden_object_ids=tuple(DRAWN))),
        ):
            with self.subTest(code=code), self.assertRaises(SectionPerspectiveError) as refused:
                freeze_section_perspective(self.repository, source=self.source, view=view, drawing_run_id="drawing-run")
            self.assertEqual(refused.exception.code, code)
            self.assertFalse((self.root / "runs" / "drawing-run").exists())
        self.assertEqual(self.repository.read_head(), self.head)


@NEEDS_OCCT
class NativeSectionPerspectiveRetentionTests(unittest.TestCase):
    def test_external_native_source_keeps_registration_import_and_frozen_view_after_reopen(self):
        import rhino3dm as rhino

        model = rhino.File3dm()
        model.Settings.ModelUnitSystem = rhino.UnitSystem.Meters
        object_id = str(model.Objects.AddBrep(rhino.Brep.CreateFromBox(rhino.Box(rhino.BoundingBox(0, 0, 0, 2, 3, 4)))))
        data = base64.b64decode(model.Encode())
        digest = hashlib.sha256(data).hexdigest()
        for schema in ("StudioModelAsset@1", "StudioExternalModelAsset@1"):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repository = FilesystemProjectRepository.initialize(
                    root, project_id="native-section", initial_state={"phase": "request", "commitments": []})
                head = repository.read_head()
                run = repository.create_run("studio-model-" + digest)
                artifact = repository.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                    artifact_id="native-box", media_type="model/vnd.rhino", source=BytesIO(data))
                source_import = {"sourceFileName": "original.skp", "conversion": {
                    "sourceFormat": "skp", "targetFormat": "3dm", "warnings": ["SKP faces are tessellated."]}}
                registration = repository.put_json(run=run,
                    destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                    record_kind=STUDIO_MODEL_ASSET, payload={
                        "schema": schema, "projectId": "native-section", "runId": run.run_id,
                        "assetSha256": digest, "representation": "external", "modelSource": None,
                        "lengthUnit": "meter", "artifact": asdict(artifact), **source_import,
                    })
                source = NativeModelSource(run.run_id, registration, artifact)
                view = SectionPerspectiveView(name="native-section", section={"line": [[0, 1.5], [2, 1.5]], "keep": "left"})
                observations = []
                drawing = freeze_section_perspective(repository, source=source, view=view, drawing_run_id="section-drawing",
                                                     operation_observer=observations.append)
                self.assertEqual(drawing.receipt["source"]["model"]["sha256"], digest)
                self.assertEqual(drawing.receipt["source"]["registration"], registration.to_dict())
                self.assertEqual(drawing.receipt["source"]["sourceImport"], source_import)
                self.assertEqual(drawing.receipt["source"]["geometry_quality"], {object_id: "exact"})
                self.assertNotIn("step", drawing.receipt["source"])
                self.assertNotIn("cad_receipt", drawing.receipt["source"])
                self.assertEqual(drawing.receipt["view"]["sourceAsset"], {"runId": run.run_id, "assetSha256": digest})
                self.assertEqual(drawing.receipt["view"]["follow"], "frozen")
                self.assertEqual(drawing.receipt["projection"]["cut_object_ids"], [object_id])
                self.assertGreater(drawing.receipt["projection"]["section_regions"], 0)
                self.assertEqual(svg_objects(drawing.svg), (object_id,))
                # A native model has no expected semantics: its lines name their object and nothing more.
                self.assertIn(b"data-object=", drawing.svg)
                self.assertNotIn(b"data-component", drawing.svg)
                self.assertNotIn(b"data-material", drawing.svg)
                self.assertTrue(all(event["details"]["input_identity"]["model_sha256"] == digest for event in observations))
                reopened = FilesystemProjectRepository.open(root)
                cold = read_model_axis_elevation(reopened, drawing.receipt_ref)
                self.assertEqual((cold.receipt, cold.svg, cold.png), (drawing.receipt, drawing.svg, drawing.png))
                again = freeze_section_perspective(reopened, source=source, view=view, drawing_run_id="section-drawing")
                self.assertEqual(again.receipt_ref, drawing.receipt_ref)
                self.assertEqual(repository.read_head(), head)
                self.assertFalse(list(root.rglob("*.step")))


if __name__ == "__main__":
    unittest.main()
