"""Explicit free positions use the existing wall and reference owners."""
from dataclasses import replace
import json
import unittest
from jsonschema import Draft202012Validator
from monkeyarch.capabilities.element_producers import ElementProducerError, ElementRow, ProductionContext, produce_rows, producer_signatures
from monkeyarch.capabilities.reference_resolver import ProjectPoint, ReferenceContext, ReferenceError, parse_reference, resolve_plan, resolve_elevation
from archflow.state.geometry_program import ProjectLevel, ProjectLevels


class GridFreeWallTests(unittest.TestCase):
    def context(self):
        levels = ProjectLevels(project_id="demo", published_by="seat-model", levels=(ProjectLevel("ground", "ground", 0, ("input:fixture",)),))
        return ProductionContext(references=ReferenceContext(grids=None, levels=levels), published={})

    def wall(self, **changes):
        row = ElementRow("free-wall", "model", "wall",
            {"base": {"level": "ground"}, "line": {"from": {"point": [1, 2]}, "to": {"point": [5, 2]}}},
            {"height": 3, "thickness": .3, "openings": [{"opening_id": "window", "kind": "window", "along": 2, "width": 1, "sill": 1, "head": 2}]},
            ("input:fixture",))
        return replace(row, **changes)

    def test_explicit_points_need_no_grid_but_remain_plan_only(self):
        context = ReferenceContext(grids=None)
        point = parse_reference({"point": [-2, 4.25]})
        self.assertIsInstance(point, ProjectPoint)
        self.assertEqual(resolve_plan(point, context), (-2, 4.25))
        self.assertIs(parse_reference(point), point)
        with self.assertRaises(ReferenceError): resolve_elevation(point, context)

    def test_malformed_points_and_unknown_references_never_fall_back(self):
        for value in ([1], [1,2,3], "1,2", {"x": 1, "z": 2}, [True, 2], [float("nan"),2], [1,float("inf")], ["1",2]):
            with self.subTest(value=value), self.assertRaises((ReferenceError, TypeError)):
                parse_reference({"point": value})
        for value in ({"grid":"missing"}, {"host":{"element":"missing", "along":0}}, {"typo":[0,0]}):
            with self.subTest(value=value), self.assertRaises(ReferenceError):
                resolve_plan(parse_reference(value), ReferenceContext(grids=None))

    def test_signature_advertises_bound_points_and_produces_the_real_hosted_cut(self):
        contract = producer_signatures()["wall"]["references"]
        bound = {"base":{"level":"ground"}, "line":{"from":{"point":[1,2]},"to":{"point":["@wall_end",2]}}}
        Draft202012Validator(contract).validate(bound)
        produced, = produce_rows((self.wall(),), self.context())
        self.assertEqual([(r.kind,r.subject,r.object) for r in produced.relations], [("hosts_void","free-wall","window")])
        self.assertIn("boolean_difference", [op.kind.value for op in produced.operations])
        body = next(op for op in produced.operations if op.op_id == "free-wall")
        params = {p.name:json.loads(p.value_json) for p in body.parameters}
        self.assertEqual(sorted(set(p[0] for p in params["profile"])), [1,5])
        self.assertEqual(params["vector"], [0,3,0])
        context = self.context()
        produce_rows((self.wall(),), context)
        self.assertEqual(resolve_plan(parse_reference({"host":{"element":"free-wall","along":2}}),context.references),(3,2))

    def test_coincident_endpoints_fail_as_a_modeling_error(self):
        references = {"base":{"level":"ground"},"line":{"from":{"point":[1,2]},"to":{"point":[1,2]}}}
        with self.assertRaisesRegex(ElementProducerError,"non-zero length"):
            produce_rows((self.wall(references=references),),self.context())
