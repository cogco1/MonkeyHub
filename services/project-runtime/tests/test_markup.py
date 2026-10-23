"""Marks on a drawn page read into the components and elements they lie on.

The page is a real ``drawing_svg`` section of a floor build-up at 1:10 (a
200 x 100 mm sheet): plywood under three joists under a finish layer, a wall
at the right end and an unbound lamp. Marks arrive as the page ink Board
feedback writes (``DocumentGesture``, page fractions), and the checks are
the change-list questions: which elements a circle names, when a mark has to
go back to the architect, and why.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import math
import unittest

from monkeycad.backends.occt.projection import OcctDrawingPolyline
from monkeydiagram.rendering.marks import (
    AREA_ZONE as AREA, POINT_ZONE as POINT, STROKE_ZONE as STROKE, ObjectInk, page_ink,
)
from monkeydiagram.rendering.svg import drawing_svg, svg_object_marks

from project_runtime.application.gestures import DocumentGesture
from project_runtime.application.markup import (
    BORDERLINE_HIT, NOTHING, PARTIAL, UNBOUND, Subject, read_id_image, read_marks, subjects_of,
)

PAGE = (200.0, 100.0)
JOISTS = {"joist-1": 0.3, "joist-2": 0.9, "joist-3": 1.5}


def _box(name: str, u0: float, v0: float, u1: float, v1: float) -> OcctDrawingPolyline:
    return OcctDrawingPolyline(name, "visible", ((u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)))


def _section() -> bytes:
    lines = [_box("obj-plywood", 0, 0, 2, 0.05), _box("obj-finish", 0, 0.40, 2, 0.45),
             _box("obj-wall", 1.9, 0, 2, 1), _box("obj-lamp", 0.6, 0.8, 0.7, 0.9)]
    lines += [_box(f"obj-{name}", u, 0.05, u + 0.1, 0.40) for name, u in JOISTS.items()]
    semantics = {f"obj-{name}": {"component": "floor"} for name in ("plywood", "finish", *JOISTS)}
    semantics["obj-wall"] = {"component": "wall-east"}
    return drawing_svg(lines, crop_uv=(0, 0, 2, 1), unit="meter", scale_denominator=10, hidden_lines=False,
                       title="C-C", semantics=semantics)


def _identify(object_id: str, component: str | None) -> Subject | None:
    if component is None:
        return None
    return Subject(component, object_id.removeprefix("obj-") if component == "floor" else None)


def _fraction(points, page=PAGE):
    return tuple((x / page[0], y / page[1]) for x, y in points)


def circle(mark_id: str, cx: float, cy: float, rx: float, ry: float | None = None, page=PAGE) -> DocumentGesture:
    ry = rx if ry is None else ry
    points = [(cx + rx * math.cos(i * math.pi / 64), cy + ry * math.sin(i * math.pi / 64)) for i in range(128)]
    return DocumentGesture(mark_id, "circle", _fraction(points, page), "#e03131", 0.004)


def outline(mark_id: str, x0: float, y0: float, x1: float, y1: float) -> DocumentGesture:
    return DocumentGesture(mark_id, "polyline", _fraction(((x0, y0), (x1, y0), (x1, y1), (x0, y1))), "#e03131",
                           0.004, closed=True)


def stroke(mark_id: str, *points, kind: str = "freehand") -> DocumentGesture:
    return DocumentGesture(mark_id, kind, _fraction(points), "#e03131", 0.004)


def subjects(reading, targets_only: bool = True) -> set[str]:
    return {hit.element_id or hit.component_id for hit in (reading.targets if targets_only else reading.hits)}


class DrawingMarkTests(unittest.TestCase):
    def setUp(self) -> None:
        size, marks = svg_object_marks(_section())
        self.assertEqual(size, PAGE)
        self.ink = page_ink(marks)

    def read(self, *gestures, ink=None, page=PAGE):
        return read_marks(gestures, self.ink if ink is None else ink, page, _identify)

    def test_a_circle_round_the_upper_layers_names_the_finish_and_every_joist_but_not_the_plywood(self) -> None:
        [reading] = self.read(circle("upper", 100, 72, 85, 20))
        self.assertEqual(reading.zone, AREA)
        self.assertFalse(reading.ambiguous)
        self.assertIsNone(reading.reason)
        self.assertEqual(subjects(reading), {"finish", "joist-1", "joist-2", "joist-3"})
        self.assertNotIn("plywood", subjects(reading, targets_only=False))
        self.assertNotIn("wall-east", subjects(reading, targets_only=False))
        joist = next(hit for hit in reading.hits if hit.element_id == "joist-2")
        self.assertEqual((joist.component_id, joist.object_ids), ("floor", ("obj-joist-2",)))

    def test_a_layer_running_on_past_the_circle_still_counts_as_circled(self) -> None:
        # The finish is 200 mm long; the circle crosses 35 mm of it near its top.
        [reading] = self.read(circle("across", 95, 70, 20))
        self.assertEqual(subjects(reading), {"finish", "joist-2"})
        finish = next(hit for hit in reading.hits if hit.element_id == "finish")
        self.assertTrue(finish.central, "it runs through the middle of the circle")
        self.assertLess(finish.coverage, 0.2, "although most of it lies outside")

    def test_of_layers_through_one_circle_those_through_its_middle_are_meant_and_those_at_its_rim_are_asked(self) -> None:
        # Five long layers through a circle of radius 10, from 0.85 of the radius below its centre to 0.85 above.
        page = (100.0, 100.0)
        layers = [ObjectInk(f"obj-{d}", "floor", lines=(((0.0, 50.0 + d), (100.0, 50.0 + d)),))
                  for d in (-8.5, -4.0, 0.0, 4.0, 8.5)]
        identify = lambda object_id, component: Subject(component, object_id)  # noqa: E731
        [reading] = read_marks([circle("ring", 50, 50, 10, page=page)], layers, page, identify)
        self.assertEqual(subjects(reading), {"obj--4.0", "obj-0.0", "obj-4.0"})
        self.assertEqual({hit.element_id for hit in reading.hits if hit.borderline}, {"obj--8.5", "obj-8.5"})
        self.assertEqual(reading.reason, BORDERLINE_HIT)
        filled = ObjectInk("obj-wall", "wall", regions=(((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),))
        [reading] = read_marks([circle("ring", 50, 50, 10, page=page)], [filled], page, identify)
        self.assertEqual(subjects(reading), {"obj-wall"}, "a body filling the circle is meant")

    def test_a_closed_outline_is_an_area_and_reads_the_same_way(self) -> None:
        [reading] = self.read(outline("box", 20, 50, 180, 93))
        self.assertEqual(reading.zone, AREA)
        self.assertEqual(subjects(reading), {"finish", "joist-1", "joist-2", "joist-3"})

    def test_a_circle_on_blank_paper_goes_back_to_the_architect(self) -> None:
        [reading] = self.read(circle("blank", 130, 25, 10))
        self.assertTrue(reading.ambiguous)
        self.assertEqual((reading.reason, reading.hits), (NOTHING, ()))

    def test_a_circle_round_ink_the_record_does_not_name_says_so(self) -> None:
        [reading] = self.read(circle("lamp", 65, 15, 9))
        self.assertTrue(reading.ambiguous)
        self.assertEqual((reading.reason, reading.hits), (UNBOUND, ()))

    def test_a_circle_that_only_grazes_two_layers_chooses_neither(self) -> None:
        # Between two joists, its top just under the finish and its bottom just over the plywood.
        [reading] = self.read(circle("between", 65, 77.5, 20))
        self.assertTrue(reading.ambiguous)
        self.assertEqual(reading.reason, PARTIAL)
        self.assertEqual(subjects(reading, targets_only=False), {"finish", "plywood"})
        self.assertTrue(all(hit.share < 0.6 and hit.coverage < 0.6 for hit in reading.hits))

    def test_layers_at_the_rim_beside_a_meant_joist_are_borderline(self) -> None:
        [reading] = self.read(circle("rim", 95, 77, 20))
        self.assertTrue(reading.ambiguous)
        self.assertEqual(reading.reason, BORDERLINE_HIT)
        self.assertEqual(subjects(reading), {"joist-2"})
        rim = {hit.element_id for hit in reading.hits if hit.borderline}
        self.assertEqual(rim, {"finish", "plywood"})
        self.assertTrue(all(not hit.central for hit in reading.hits if hit.borderline))

    def test_a_circle_on_the_joint_of_two_things_names_both(self) -> None:
        # Centred where joist-1's top corner meets the finish's underside.
        [reading] = self.read(circle("corner", 40, 60, 5))
        self.assertEqual(subjects(reading), {"finish", "joist-1"})
        self.assertIsNone(reading.reason)

    def test_a_small_circle_on_one_edge_is_on_that_thing(self) -> None:
        [reading] = self.read(circle("edge", 120, 96, 2))
        self.assertEqual(subjects(reading), {"plywood"})
        [hit] = reading.hits
        self.assertEqual(hit.share, 1.0)

    def test_a_circle_inside_a_layer_touching_none_of_its_lines_is_on_that_layer(self) -> None:
        [reading] = self.read(circle("inside", 95, 77, 2))
        self.assertEqual(subjects(reading), {"joist-2"})
        [reading] = self.read(circle("gap", 65, 77, 4))
        self.assertEqual(reading.reason, NOTHING, "between joists nothing is drawn")

    def test_an_arrow_points_at_what_its_tip_is_on(self) -> None:
        [reading] = self.read(stroke("arrow", (60, 30), (95, 75), kind="arrow"))
        self.assertEqual((reading.zone, reading.kind), (POINT, "arrow"))
        self.assertEqual(subjects(reading), {"joist-2"})
        [reading] = self.read(stroke("arrow", (60, 30), (91, 75), kind="arrow"))
        self.assertEqual(subjects(reading), {"joist-2"}, "near its edge, still inside its outline")
        [reading] = self.read(stroke("arrow", (60, 30), (130, 25), kind="arrow"))
        self.assertEqual(reading.reason, NOTHING)

    def test_an_open_stroke_underlining_one_layer_is_on_it_and_one_striking_through_names_each_it_crosses(self) -> None:
        [reading] = self.read(stroke("under", (60, 54.5), (170, 54.5), kind="line"))
        self.assertEqual(reading.zone, STROKE)
        self.assertEqual(subjects(reading), {"finish"})
        [reading] = self.read(stroke("strike", (95, 50), (95, 99), kind="line"))
        self.assertEqual(subjects(reading), {"finish", "joist-2", "plywood"})

    def test_an_open_freehand_scribble_is_a_stroke_and_one_that_closes_is_an_area(self) -> None:
        [scribble] = self.read(stroke("scribble", (60, 54.5), (100, 55), (140, 54.5), (170, 55)))
        self.assertEqual(scribble.zone, STROKE)
        loop = [(100 + 85 * math.cos(a / 10), 72 + 20 * math.sin(a / 10)) for a in range(60)]
        [lasso] = self.read(stroke("lasso", *loop))
        self.assertEqual(lasso.zone, AREA)
        self.assertEqual(subjects(lasso), {"finish", "joist-1", "joist-2", "joist-3"})

    def test_a_sheet_reads_a_view_at_its_own_paper_position(self) -> None:
        sheet = (420.0, 297.0)
        _, marks = svg_object_marks(_section())
        placed = page_ink(marks, place=(20.0, 30.0))
        [reading] = read_marks((circle("upper", 120, 102, 85, 20, page=sheet),), placed, sheet, _identify)
        self.assertEqual(subjects(reading), {"finish", "joist-1", "joist-2", "joist-3"})

    def test_text_is_the_sentence_not_a_mark_and_the_answer_is_the_same_every_time(self) -> None:
        note = DocumentGesture("note", "text", ((0.1, 0.1),), "#000000", 0.004, label="只留胶合板", font_size=0.03)
        marks = (note, circle("upper", 100, 72, 85, 20), circle("blank", 130, 25, 10))
        first = self.read(*marks)
        self.assertEqual([reading.mark_id for reading in first], ["upper", "blank"])
        self.assertEqual(first, self.read(*marks))
        self.assertEqual(first[1].to_dict(), {"markId": "blank", "kind": "circle", "zone": AREA, "hits": [],
                                              "ambiguous": True, "reason": NOTHING})
        targets = first[0].to_dict()["hits"]
        self.assertTrue(all(row["target"] for row in targets[:4]))
        self.assertEqual(set(targets[0]), {"componentId", "elementId", "objectIds", "coverage", "share", "central",
                                           "target", "borderline"})


class SubjectTests(unittest.TestCase):
    def test_an_object_answers_through_its_named_component_and_that_components_own_elements(self) -> None:
        components = [SimpleNamespace(entity_id="floor"), SimpleNamespace(entity_id="wall-east")]
        projection = SimpleNamespace(
            record=SimpleNamespace(entities_of=lambda kind: components if kind == "Component@1" else []),
            elements=(SimpleNamespace(component_id="floor", element_id="joist"),
                      SimpleNamespace(component_id="floor", element_id="joist-2"),
                      SimpleNamespace(component_id="wall-east", element_id="plywood")))
        identify = subjects_of(projection)
        self.assertEqual(identify("obj-joist-2-0", "floor"), Subject("floor", "joist-2"))
        self.assertEqual(identify("obj-plywood", "floor"), Subject("floor", None),
                         "another component's element is not this one's")
        self.assertEqual(identify("obj-wall", "wall-east"), Subject("wall-east", None))
        self.assertIsNone(identify("obj-joist-2", "roof"), "a component the record does not declare")
        self.assertIsNone(identify("obj-joist-2", None), "a name alone binds nothing")


class IdImageTests(unittest.TestCase):
    """A 100 x 50 object-ID image: slab across the bottom, a column, white background."""

    legend = ({"key": "obj-slab", "color": "#1F77B4", "objects": 1, "pixels": 0},
              {"key": "obj-column", "color": "#FF7F0E", "objects": 1, "pixels": 0},
              {"key": "A-FURNITURE", "color": "#2CA02C", "objects": 3, "pixels": 0})
    components = {"obj-slab": "floor", "obj-column": "frame"}

    def png(self) -> bytes:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (100, 50), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 40, 99, 49), fill=(0x1F, 0x77, 0xB4))
        draw.rectangle((45, 10, 54, 39), fill=(0xFF, 0x7F, 0x0E))
        draw.rectangle((5, 5, 20, 20), fill=(0x2C, 0xA0, 0x2C))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def read(self, *gestures):
        identify = lambda object_id, component: None if component is None else Subject(component)  # noqa: E731
        return read_id_image(gestures, self.png(), self.legend, self.components, identify)

    def test_a_circle_round_the_column_names_it_and_one_on_the_slab_names_the_slab(self) -> None:
        page = (100.0, 50.0)
        [column, slab] = self.read(circle("column", 50, 25, 12, 17, page=page), circle("slab", 30, 45, 4, page=page))
        self.assertEqual(subjects(column), {"frame"})
        self.assertEqual(column.targets[0].coverage, 1.0)
        self.assertEqual(subjects(slab), {"floor"})

    def test_background_is_nothing_and_a_layer_colour_names_no_object(self) -> None:
        page = (100.0, 50.0)
        [blank, furniture] = self.read(circle("blank", 80, 20, 6, page=page), circle("chair", 12, 12, 5, page=page))
        self.assertEqual(blank.reason, NOTHING)
        self.assertEqual((furniture.reason, furniture.hits), (UNBOUND, ()))

    def test_an_arrow_on_the_image_points_at_the_colour_under_its_tip(self) -> None:
        [reading] = self.read(DocumentGesture("arrow", "arrow", ((0.2, 0.1), (0.5, 0.5)), "#e03131", 0.004))
        self.assertEqual(subjects(reading), {"frame"})


if __name__ == "__main__":
    unittest.main()
