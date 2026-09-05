"""P100: derivation table + semantic references.

Numbers get names and bases; elements are placed by reference and lowered
through one resolver; elevation references stay symbolic for the compiler.
"""
from __future__ import annotations

import unittest

from archflow.capabilities.reference_resolver import (
    AxisPoint,
    GridIntersection,
    GridRef,
    HostAlong,
    HostLine,
    LevelRef,
    OffsetFrom,
    ReferenceContext,
    ReferenceError,
    parse_reference,
    resolve_elevation,
    resolve_plan,
)
from archflow.state.derivation import (
    DerivationError,
    DerivationTable,
    DerivedQuantity,
    evaluate,
    expression_names,
    substitute,
)
from archflow.state.geometry_program import ProjectGridAxis, ProjectGrids, ProjectLevel, ProjectLevels

BASIS = ("reading:demo",)


def _table() -> DerivationTable:
    return DerivationTable("demo", (
        DerivedQuantity("body_side", "21.42", "m", BASIS, "declared"),
        DerivedQuantity("body_half", "body_side / 2", "m"),
        DerivedQuantity("column_diameter", "0.714", "m", BASIS, "declared"),
        DerivedQuantity("column_height", "9 * column_diameter", "m", ("rule:ionic-nine-diameters",)),
        DerivedQuantity("axis_spacing", "2.25 * column_diameter", "m"),
        DerivedQuantity("stair_riser", "service_top / risers", "m"),
        DerivedQuantity("clamped", "min(max(body_half, 5), 12) + abs(-1) + round(sqrt(16), 0)", "m"),
    ))


def _grids() -> ProjectGrids:
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=(
        ProjectGridAxis("axis-1", "1", (-10.71, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS),
        ProjectGridAxis("axis-6", "6", (10.71, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS),
        ProjectGridAxis("axis-a", "A", (0.0, 0.0, -10.71), (1.0, 0.0, 0.0), BASIS),
        ProjectGridAxis("axis-f", "F", (0.0, 0.0, 10.71), (1.0, 0.0, 0.0), BASIS),
    ))


def _levels() -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-eaves", "eaves", 13.388, BASIS), ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS),
        ProjectLevel("level-piano-nobile", "piano-nobile", 3.57, BASIS)))


class DerivationTests(unittest.TestCase):
    def test_quantities_evaluate_in_dependency_order_with_inputs(self) -> None:
        derived = evaluate(_table(), {"service_top": 3.57, "risers": 23})
        self.assertAlmostEqual(derived["column_height"], 6.426)
        self.assertAlmostEqual(derived["axis_spacing"], 1.6065)
        self.assertAlmostEqual(derived["stair_riser"], 3.57 / 23)
        self.assertAlmostEqual(derived["clamped"], 10.71 + 1 + 4)
        by_name = {v.name: v for v in derived.values}
        self.assertEqual(by_name["column_height"].inputs, ("column_diameter",))
        self.assertEqual(by_name["stair_riser"].inputs, ("service_top", "risers"))
        self.assertEqual(by_name["column_height"].epistemic_status, "derived")
        self.assertEqual(len(derived.digest), 64)
        self.assertEqual(DerivationTable.from_dict(_table().to_dict()), _table())
        self.assertEqual(expression_names("a + b * (c - a)"), ("a", "b", "c"))

    def test_names_are_read_off_the_grammar_without_computing_anything(self) -> None:
        """``a / (a - 1)`` names ``a``; whether it divides by zero is the readings' question, asked at evaluation."""

        self.assertEqual(expression_names("source / (source - 1)"), ("source",))            # used to raise division by zero on a placeholder
        self.assertEqual(expression_names("sqrt(a - b) / min(c, 0)"), ("a", "b", "c"))          # no sqrt of a negative, no division by zero: nothing ran
        table = DerivationTable("demo", (DerivedQuantity("ratio", "source / (source - 1)", "-"),))
        self.assertAlmostEqual(evaluate(table, {"source": 3.0})["ratio"], 1.5)
        with self.assertRaisesRegex(DerivationError, "division by zero"):
            evaluate(table, {"source": 1.0})                                                    # the real reading still fails, where it should
        # the grammar is still validated in full while names are read
        for bad in ("a +", "a $ b", "unknownfn(a)", "min(a)", "(a", "a b"):
            with self.assertRaises(DerivationError):
                expression_names(bad)

    def test_gaps_fail_typed(self) -> None:
        with self.assertRaises(DerivationError):
            evaluate(_table())                                              # service_top / risers unknown
        with self.assertRaises(DerivationError):
            evaluate(DerivationTable("d", (DerivedQuantity("a", "b + 1", "m"), DerivedQuantity("b", "a * 2", "m"))))
        with self.assertRaises(DerivationError):
            evaluate(DerivationTable("d", (DerivedQuantity("a", "1 / (2 - 2)", "m"),)))
        with self.assertRaises(DerivationError):
            DerivedQuantity("a", "import os", "m")
        with self.assertRaises(DerivationError):
            DerivedQuantity("a", "a ** 2", "m")
        with self.assertRaises(DerivationError):
            evaluate(DerivationTable("d", (DerivedQuantity("x", "1", "m"),)), {"x": 2.0})   # shadowing a reading

    def test_substitution_replaces_named_quantities_anywhere(self) -> None:
        derived = evaluate(_table(), {"service_top": 3.57, "risers": 23})
        value = substitute({"spacing": "@axis_spacing", "list": ["@column_height", 1.0], "text": "plain"}, derived)
        self.assertAlmostEqual(value["spacing"], 1.6065)
        self.assertAlmostEqual(value["list"][0], 6.426)
        self.assertEqual(value["text"], "plain")
        with self.assertRaises(DerivationError):
            substitute("@nothing", derived)


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ReferenceContext(grids=_grids(), levels=_levels(), hosts={"wall-west": HostLine((-10.71, -10.71), (0.0, 1.0))})

    def test_plan_references_resolve_from_the_grids_and_hosts(self) -> None:
        self.assertEqual(resolve_plan(GridIntersection("1", "A"), self.context), (-10.71, -10.71))
        self.assertEqual(resolve_plan(GridIntersection("F", "6"), self.context), (10.71, 10.71))
        self.assertEqual(resolve_plan(AxisPoint("1", 5.0), self.context), (-10.71, 5.0))
        self.assertEqual(resolve_plan(GridRef("A"), self.context), (0.0, -10.71))
        self.assertEqual(resolve_plan(HostAlong("wall-west", 3.16), self.context), (-10.71, -7.55))
        self.assertEqual(resolve_plan(HostAlong("wall-west", 3.16, across=0.42), self.context), (-10.29, -7.55))
        with self.assertRaises(ReferenceError):
            resolve_plan(GridIntersection("1", "6"), self.context)               # parallel
        with self.assertRaises(ReferenceError):
            resolve_plan(HostAlong("wall-north", 1.0), self.context)

    def test_a_reference_names_a_grid_role_exactly(self) -> None:
        with self.assertRaises(ReferenceError) as raised:
            resolve_plan(GridRef("axis-1"), self.context)                        # the axis id is not a role: no fallback
        self.assertIn("axis-1", str(raised.exception))
        self.assertIn("1, 6, A, F", str(raised.exception))                       # the roles that do exist

    def test_elevation_references_stay_symbolic(self) -> None:
        self.assertEqual(resolve_elevation(LevelRef("level-piano-nobile"), self.context), ("level-piano-nobile", 0.0))
        self.assertEqual(resolve_elevation(OffsetFrom("level-piano-nobile", 1.53), self.context), ("level-piano-nobile", 1.53))
        with self.assertRaises(ReferenceError):
            resolve_elevation(LevelRef("level-attic"), self.context)
        self.assertEqual(parse_reference({"grid": ["A", "1"]}), GridIntersection("A", "1"))
        self.assertEqual(parse_reference({"offset_from": {"level": "level-ground", "offset": 0.45}}), OffsetFrom("level-ground", 0.45))


if __name__ == "__main__":
    unittest.main()
