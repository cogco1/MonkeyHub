"""Stairs and railings in a construction script (gap 34).

A greybox stair was 40 step blocks and a railing a slab drawn on a side plane: nothing said either was a stair or
a railing, and a new rise meant a new script. ``stair()`` makes one straight flight by the producer a runner record
already uses, and ``railing()`` posts and a rail along a polyline; both are edited by id afterwards like any shape.
"""

from __future__ import annotations

import unittest

from .test_construction_routes import ConstructionTestCase

FLIGHT = "\n".join([
    "flight = stair((0, 0), (3, 0), 10, 0.18, 1.2)",
    "landing = extrude(rect(3, -0.6, 1.2, 1.2), -0.2, at=top(flight))",
    "guard = railing([(0, 0, 0.6), (3, 1.8, 0.6), (4.2, 1.8, 0.6)], height=0.9)",
])


def close(case: unittest.TestCase, got, expected, places: int = 6) -> None:
    for a, b in zip([c for corner in got for c in corner], [c for corner in expected for c in corner]):
        case.assertAlmostEqual(a, b, places=places)


class StairAndRailingTestCase(ConstructionTestCase):
    cad_export = "occt"

    def test_a_flight_carries_its_landing_and_a_railing_follows_both(self) -> None:
        made = self.construct(FLIGHT)
        rows = {row["entity_id"]: row["fields"] for row in made["change"]["edits"]["entities"]
                if row["schema"] == "Element@1"}
        flight = rows["flight-body"]
        self.assertEqual(flight["producer"], "stair")
        self.assertEqual(flight["params"], {"count": 10, "rise": 0.18, "width": 1.2})
        self.assertEqual((flight["references"]["from"], flight["references"]["to"]),
                         ({"point": [0, 0]}, {"point": [3, 0]}))
        self.assertEqual(rows["guard-body"]["producer"], "railing")
        self.assertEqual(rows["landing-body"]["references"]["base"], {"datum": "flight-body-top"},
                         "the landing stands on the flight's top")

        run = self.run_candidate(made["proposalId"])
        candidate = self.client.get(f"/api/candidates/{run}").json()
        self.assertIsNone(candidate["objectReadbackError"], candidate)
        objects = {row["name"] for row in candidate["objects"]}
        self.assertIn("obj-flight-body", objects)
        # Posts at every point and at most 1.2 apart: three on the 3 m run, one on the landing, one at the end.
        self.assertEqual(sorted(name for name in objects if "-post-" in name),
                         [f"obj-guard-body-post-{k}" for k in range(5)])
        self.assertEqual(sorted(name for name in objects if "-rail-" in name),
                         ["obj-guard-body-rail-0", "obj-guard-body-rail-1"])

        bounds = {row["id"]: row["bounds"] for row in self.model(run)["entities"]}
        close(self, bounds["flight"], [[0, 0, -0.6], [3, 1.8, 0.6]])
        close(self, bounds["landing"], [[3, 1.6, -0.6], [4.2, 1.8, 0.6]])
        # The rail's top runs 0.9 above the line; posts are 0.05 square.
        close(self, bounds["guard"], [[-0.025, 0, 0.575], [4.225, 2.7, 0.625]], places=2)

        # Edited by id later: the flight moves with its landing, and cannot be scaled.
        moved = self.construct('move(get("flight"), dx=1)\nmove(get("landing"), dx=1)', sourceRunId=run)
        [row] = [row for row in moved["change"]["edits"]["entities"] if row["entity_id"] == "flight-body"]
        self.assertEqual((row["fields"]["references"]["from"], row["fields"]["references"]["to"]),
                         ({"point": [1, 0]}, {"point": [4, 0]}))
        scaled = self.construct('scale(get("flight"), 2)', sourceRunId=run, expect=422)
        self.assert_refused_at(scaled, 1, 'scale(get("flight"), 2)')
        self.assertIn("not scaled", scaled["message"])

    def test_a_stair_or_railing_that_cannot_be_built_is_refused_at_its_line(self) -> None:
        for script, line, words in (
                ("s = stair((0, 0), (3, 0), 0, 0.18, 1.2)", "s = stair((0, 0), (3, 0), 0, 0.18, 1.2)", "at least one step"),
                ("s = stair((0, 0), (0, 0), 10, 0.18, 1.2)", "s = stair((0, 0), (0, 0), 10, 0.18, 1.2)", "the same point"),
                ("r = railing([(0, 0, 0), (0, 2, 0)])", "r = railing([(0, 0, 0), (0, 2, 0)])", "vertical"),
                ("r = railing([(0, 0, 0), (2, 0, 0)], height=0.04)", "r = railing([(0, 0, 0), (2, 0, 0)], height=0.04)",
                 "higher than its rail")):
            with self.subTest(script=script):
                refused = self.construct(script, expect=422)
                self.assert_refused_at(refused, 1, line)
                self.assertIn(words, refused["message"])

    def test_the_construction_vocabulary_names_both(self) -> None:
        verbs = {verb["name"]: verb for verb in self.client.get("/api/construction").json()["verbs"]}
        self.assertEqual(verbs["stair"]["signature"], "stair(start, end, count, rise, width, at=0)")
        self.assertEqual(verbs["railing"]["signature"],
                         "railing(points, height=1.0, post=0.05, spacing=1.2, rail=0.05)")
