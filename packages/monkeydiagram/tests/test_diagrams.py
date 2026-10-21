"""Diagrams drawn from data (gap 38): the checks, the routing and the two styles of a network."""

import os
from pathlib import Path
import unittest

from monkeydiagram import diagrams
from monkeydiagram.diagrams import DiagramError, TONES, check, draw


def fonts():
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for normal, bold in ((windows / "arial.ttf", windows / "arialbd.ttf"),
                         (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                          Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))):
        if normal.is_file() and bold.is_file():
            return {"normal": normal, "bold": bold}
    raise unittest.SkipTest("no regular and bold TTF to measure text with")


def node(nid, x, y, group=None):
    return {"id": nid, "label": nid, "at": [x, y], "size": [20, 10], **({"group": group} if group else {})}


class NetworkCheckTests(unittest.TestCase):
    def test_what_cannot_be_drawn_is_named_by_its_path(self):
        good = {"nodes": [node("a", 0, 0), node("b", 50, 0)], "edges": [{"from": "a", "to": "b"}]}
        self.assertEqual(check("network", good)["edges"][0]["arrow"], "end")
        for spec, path in (({"nodes": []}, "spec.nodes"),
                           ({"nodes": [node("a", 0, 0), node("a", 5, 5)]}, "spec.nodes[1].id"),
                           ({"nodes": [{"id": "a", "label": "a", "at": [0]}]}, "spec.nodes[0].at"),
                           ({"nodes": [node("a", 0, 0)], "edges": [{"from": [0, 0], "to": [5, 5]}]}, "spec.edges[0]"),
                           ({"nodes": [node("a", 0, 0)], "edges": [{"from": "a", "to": "b"}]}, "spec.edges[0].to"),
                           ({"nodes": [node("a", 0, 0, "g")]}, "spec.nodes[0].group"),
                           ({**good, "edges": [{"from": "a", "to": "b", "style": "long"}]}, "spec.edges[0].style"),
                           ({**good, "legend": True}, "spec")):
            with self.subTest(path=path), self.assertRaises(DiagramError) as caught:
                check("network", spec)
            self.assertEqual(caught.exception.path, path)
        with self.assertRaises(DiagramError):
            check("mindmap", good)

    def test_a_branch_has_no_arrow_and_a_via_edge_runs_straight_through_its_points(self):
        spec = check("network", {"nodes": [node("a", 0, 0), node("b", 50, 0)],
                                 "edges": [{"from": "a", "to": [0, 40], "line": "branch"},
                                           {"from": "a", "to": "b", "via": [[25, 20]]}]})
        self.assertEqual((spec["edges"][0]["arrow"], spec["edges"][1]["route"]), ("none", "straight"))


class NetworkRoutingTests(unittest.TestCase):
    def test_an_edge_turns_the_other_way_round_a_node_in_its_path(self):
        # a at the top left, b at the bottom right, c on the run that leaves a along x first.
        spec = check("network", {"nodes": [node("a", 0, 0), node("b", 100, 100), node("c", 50, 0)],
                                 "edges": [{"from": "a", "to": "b"}]})
        nodes = {row["id"]: row for row in spec["nodes"]}
        points, crossing = diagrams._route(spec["edges"][0], nodes)
        self.assertFalse(crossing)
        self.assertEqual(points[0], (0.0, 5.0), "it leaves a's bottom, not its right side toward c")

    def test_an_edge_with_no_clear_way_says_so(self):
        spec = check("network", {"nodes": [node("a", 0, 0), node("b", 100, 0), node("c", 50, 0)],
                                 "edges": [{"from": "a", "to": "b", "route": "straight"}]})
        drawn = draw("network", {"nodes": [node("a", 0, 0), node("b", 100, 0), node("c", 50, 0)],
                                 "edges": [{"from": "a", "to": "b", "route": "straight"}]},
                     style_id="diagram-colour", title="t", font_mapping=fonts())
        self.assertTrue(diagrams._route(spec["edges"][0], {row["id"]: row for row in spec["nodes"]})[1])
        self.assertEqual(drawn.warnings, ("edge 0 (a to b) runs through another node; give it via points",))


class NetworkStyleTests(unittest.TestCase):
    SPEC = {"groups": [{"id": "g", "label": "Ground", "tone": "sand"}],
            "nodes": [node("a", 0, 0, "g"), node("b", 60, 0)],
            "edges": [{"from": "a", "to": "b", "line": "loop"}], "north": 0, "scale": {"length": 20, "label": "20 m"}}

    def fills(self, style):
        canvas = draw("network", self.SPEC, style_id=style, title="Plan", font_mapping=fonts()).canvas
        return {tuple(round(c, 2) for c in p.data["fill_color"][:3]) for p in canvas.scenes[0].primitives
                if p.kind == "path" and p.data["fill"]}

    def test_colour_fills_its_groups_and_mono_fills_none(self):
        sand = tuple(round(c, 2) for c in TONES["sand"])
        self.assertIn(sand, self.fills("diagram-colour"))
        self.assertNotIn(sand, self.fills("diagram-mono"))

    def test_mono_tells_lines_apart_by_their_dash(self):
        canvas = draw("network", self.SPEC, style_id="diagram-mono", title="Plan", font_mapping=fonts()).canvas
        dashes = {p.data["dash_pt"] for p in canvas.scenes[0].primitives if p.kind == "path" and p.data["stroke"]}
        self.assertTrue(any(len(dash) == 2 and dash[0] > 10 for dash in dashes), "the loop has its long dash")
        with self.assertRaises(DiagramError):
            draw("network", self.SPEC, style_id="neon", title="Plan", font_mapping=fonts())


SECTION = {"axis": {"x": [0, 100], "y": [0, 50], "ticks": {"y": [0, 50]}},
           "ground": {"y": 0, "label": "ground"},
           "spans": [{"id": "hall", "label": "Hall", "x0": 10, "x1": 40, "y0": 0, "y1": 12, "tone": "grey", "floors": [6]},
                     {"id": "deck", "x0": 30, "x1": 90, "y0": 40, "y1": 50, "tone": "lavender", "labelAt": "below"}],
           "shapes": [{"kind": "polygon", "points": [[50, 40], [60, 25], [70, 40]]},
                      {"kind": "marker", "at": [60, 25], "tone": "sand", "line": "loop"}],
           "moves": [{"from": [45, 2], "to": [45, 38], "line": "return", "label": "lift"}]}
TIMELINE = {"axis": {"start": 0, "end": 10, "step": 1, "unit": "min"},
            "lanes": [{"id": "zones", "label": "Zones"}, {"id": "saves", "label": "Saves"}],
            "segments": [{"lane": "zones", "start": 1, "end": 4, "label": "P1", "colour": "grey"},
                         {"lane": "zones", "start": 4, "end": 9, "label": "02", "colour": "#5c7cfa"}],
            "events": [{"lane": "saves", "at": 3, "mark": "tick", "colour": "#f59f00"},
                       {"lane": "zones", "at": 6, "mark": "diamond", "label": "slide"}]}


class SectionAndTimelineTests(unittest.TestCase):
    def test_what_cannot_be_drawn_is_named_by_its_path(self):
        self.assertEqual(check("section", SECTION)["spans"][0]["floors"], [6.0])
        self.assertEqual(check("timeline", TIMELINE)["segments"][1]["colour"], (92 / 255, 124 / 255, 250 / 255))
        for kind, spec, path in (
                ("section", {**SECTION, "axis": {"x": [10, 0], "y": [0, 50]}}, "spec.axis.x"),
                ("section", {**SECTION, "spans": [{"id": "s", "x0": 0, "x1": 5, "y0": 0, "y1": 5, "floors": [9]}]},
                 "spec.spans[0].floors"),
                ("section", {**SECTION, "shapes": [{"kind": "polygon", "points": [[0, 0], [1, 1]]}]}, "spec.shapes[0].points"),
                ("timeline", {**TIMELINE, "events": [{"lane": "nowhere", "at": 1}]}, "spec.events[0].lane"),
                ("timeline", {**TIMELINE, "segments": [{"lane": "zones", "start": 2, "end": 12}]}, "spec.segments[0].end"),
                ("timeline", {**TIMELINE, "segments": [{"lane": "zones", "start": 2, "end": 3, "colour": "#zzzzzz"}]},
                 "spec.segments[0].colour")):
            with self.subTest(path=path), self.assertRaises(DiagramError) as caught:
                check(kind, spec)
            self.assertEqual(caught.exception.path, path)

    def test_a_section_keeps_one_scale_and_a_timeline_its_colours_unless_mono(self):
        canvas = draw("section", SECTION, style_id="diagram-colour", title="S", font_mapping=fonts()).canvas
        boxes = [p.bounds_mm for p in canvas.scenes[0].primitives if p.kind == "path" and p.data["fill"]
                 and tuple(round(c, 2) for c in p.data["fill_color"][:3]) == tuple(round(c, 2) for c in TONES["grey"])]
        (x0, y0, x1, y1), = boxes[:1]
        self.assertAlmostEqual((x1 - x0) / (y1 - y0), 30 / 12, delta=0.05, msg="a span is as wide for its height as its spec")
        blue = (round(92 / 255, 2), round(124 / 255, 2), round(250 / 255, 2))

        def fills(style):
            scene = draw("timeline", TIMELINE, style_id=style, title="T", font_mapping=fonts()).canvas.scenes[0]
            return {tuple(round(c, 2) for c in p.data["fill_color"][:3]) for p in scene.primitives
                    if p.kind == "path" and p.data["fill"]}

        self.assertIn(blue, fills("diagram-colour"))
        self.assertNotIn(blue, fills("diagram-mono"))
