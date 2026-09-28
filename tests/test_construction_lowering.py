"""#419: a construction script lowers to Component@1/Element@1 rows; producers are chosen here only.

The record is what ``initialize_modeling`` seeds (a modelling root ``model`` and a level
``ground`` at zero). Every lowered result is applied the way the semantic-edit path applies
it (existing fields merged), checked by ``validate_element_contract`` and produced. Ids are
neutral (``mass``, ``block``, ``cutter``): nothing here says what the geometry is.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import unittest
from types import SimpleNamespace

from archflow.project.refs import ProjectVersionRef
from archflow.state.geometry_program import delivered_object_ids
from archflow.state.state_record import (
    Entity,
    Parameter,
    StateRecord,
    apply_state_record_operator,
    compile_component_edit,
    project_grids_of,
    project_levels_of,
)
from monkeyarch.capabilities.element_producers import (
    ProductionContext,
    element_rows_of,
    produce_rows,
    validate_element_contract,
)
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.construction import (
    ELEMENT_SUFFIX,
    ConstructionError,
    compile_construction_script,
    geometry_view,
    vocabulary,
)
from monkeyarch.construction.vocabulary import layer_rule_violations

EVIDENCE = "input:monkeyarch-modeling-setup"
PARAMETERS = (
    Parameter("h", 3.0, "m", epistemic_status="declared", source_ref=EVIDENCE),
    Parameter("sill", 0.9, "m", epistemic_status="declared", source_ref=EVIDENCE),
)
ROOT = Entity("model", "Component@1", {"intent": "Root for candidate modeling", "source_refs": [EVIDENCE]})


def _level(entity_id: str, elevation: float, role: str | None = None) -> Entity:
    return Entity(entity_id, "Level@1", {"role": role or entity_id, "elevation": elevation}, basis_refs=(EVIDENCE,))


def _record(*extra: Entity, parameters: tuple[Parameter, ...] = (), ground: float | None = 0.0) -> StateRecord:
    levels = (_level("ground", ground),) if ground is not None else ()
    return StateRecord(
        project_id="demo", run_id="authored", entities=(ROOT, *levels, *extra),
        parameters=parameters, evidence_refs=(EVIDENCE,), option={"option_id": "modeling"},
        base=ProjectVersionRef("demo", 0, "0" * 64),
    )


def _compile(script: str, record: StateRecord | None = None):
    return compile_construction_script(script, record if record is not None else _record(), root_component_id="model")


def _apply(record: StateRecord, result) -> StateRecord:
    """The successor, exactly as the semantic-edit path derives it: rows merged over existing fields."""

    existing = {entity.entity_id: entity for entity in record.entities}
    entities = []
    for row in result.entities:
        previous = existing.get(row["entity_id"])
        value = dict(row)
        if previous is not None:
            value = {**previous.to_dict(), **value, "fields": {**previous.fields, **row["fields"]}}
        entities.append(Entity.from_dict(value))
    operator = compile_component_edit(record, entities=tuple(entities), remove_entity_ids=tuple(result.remove_entity_ids))
    successor = apply_state_record_operator(record, operator)
    validate_element_contract(successor, tuple(row["entity_id"] for row in result.entities if row["schema"] == "Element@1"))
    return successor


def _operations(record: StateRecord) -> dict:
    context = ProductionContext(references=ReferenceContext(grids=project_grids_of(record), levels=project_levels_of(record)),
                                published={})
    produced = produce_rows(element_rows_of(record), context)
    return {operation.op_id: operation for element in produced for operation in element.operations}


def _params(operation) -> dict:
    return {parameter.name: json.loads(parameter.value_json) for parameter in operation.parameters}


def _view(record: StateRecord) -> dict:
    return {row["id"]: row for row in geometry_view(record)}


def _elements(result) -> dict:
    return {row["entity_id"]: row for row in result.entities if row["schema"] == "Element@1"}


def _hashed(prefix: str, *statements: str, index: int = 0) -> str:
    """An unnamed shape's id as the vocabulary states it: twelve hex digits of the sha1 of the statements that made
    it (each as its tokens one space apart; the module-level statement first, then each statement it called, down to
    the one that made the shape) and of how many shapes that chain of statements made before it."""

    return prefix + hashlib.sha1(("\x00".join(statements) + f"\n{index}").encode("utf-8")).hexdigest()[:12]


LOOP = "\n".join([
    "mass = extrude(rect(0, 0, 12, 8), 3)",
    "for i in range(4):",
    "    w = extrude(rect(1 + 2.8 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)",
    "    cut(mass, w)",
])
CUTTERS = ["w-1-body", "w-2-body", "w-3-body", "w-4-body"]


class ConstructionTestCase(unittest.TestCase):
    def refused(self, script: str, record: StateRecord | None = None) -> ConstructionError:
        with self.assertRaises(ConstructionError) as caught:
            _compile(script, record)
        self.assertEqual(layer_rule_violations(caught.exception.message), (), caught.exception.message)
        return caught.exception

    def refusals(self, cases, record: StateRecord | None = None) -> None:
        for script, line, words in cases:
            with self.subTest(script=script):
                error = self.refused(script, record)
                self.assertEqual(error.line, line, error.message)
                self.assertIn(words, error.message)

    def cut_record(self) -> StateRecord:
        record = _record()
        return _apply(record, _compile(LOOP, record))


class ExtrusionLoweringTests(ConstructionTestCase):
    def test_one_extrusion_is_exactly_the_rows_of_the_spec_table(self) -> None:
        record = _record()
        result = _compile("mass = extrude(rect(0, 0, 4, 3), 3)", record)
        self.assertEqual(ELEMENT_SUFFIX, "-body")
        self.assertEqual(result.entities, (
            {"entity_id": "mass", "schema": "Component@1", "parent_id": "model", "fields": {"intent": "mass"}},
            {"entity_id": "mass-body", "schema": "Element@1", "parent_id": "mass", "fields": {
                "component_id": "mass", "producer": "prism",
                "references": {"base": {"level": "ground"}},
                "params": {"profile": [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]], "height": 3.0}}},
        ))
        self.assertEqual(result.remove_entity_ids, ())
        self.assertEqual(result.report, ({"id": "mass", "form": "solid", "status": "created",
                                          "bounds": [[0.0, 0.0, 0.0], [4.0, 3.0, 3.0]], "cuts": [], "line": 1},))
        self.assertEqual(result.summary, "construction: create mass")
        self.assertEqual(dict(result.line_of), {"mass": 1, "mass-body": 1})
        successor = _apply(record, result)
        operation = _operations(successor)["mass-body"]
        self.assertEqual(operation.output_object_ids, ("obj-mass-body",))
        self.assertEqual(_params(operation)["vector"], [0.0, 3.0, 0.0])

    def test_a_block_stacked_on_top_follows_the_published_top(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "a = extrude(rect(0, 0, 4, 3), 3)",
            "b = extrude(rect(1, 1, 2, 1), 2, at=top(a))",
            "c = extrude(rect(1, 1, 1, 1), 1, at=top(b) + 0.5)",
            "print(bounds(b), bounds(c))",
        ]), record)
        elements = _elements(result)
        self.assertEqual(elements["b-body"]["fields"]["references"], {"base": {"datum": "a-body-top"}})
        self.assertNotIn("elevation", elements["b-body"]["fields"]["params"])
        self.assertEqual(elements["c-body"]["fields"]["references"], {"base": {"datum": "b-body-top"}})
        self.assertEqual(elements["c-body"]["fields"]["params"]["elevation"], 0.5)
        self.assertEqual(result.log, ("((1.0, 3.0, 1.0), (3.0, 5.0, 2.0)) ((1.0, 5.5, 1.0), (2.0, 6.5, 2.0))",))
        view = _view(_apply(record, result))
        self.assertEqual(view["b"]["bounds"], [[1.0, 3.0, 1.0], [3.0, 5.0, 2.0]])
        self.assertEqual(view["c"]["bounds"], [[1.0, 5.5, 1.0], [2.0, 6.5, 2.0]])

    def test_a_negative_height_goes_down_like_a_drawn_sketch(self) -> None:
        record = _record()
        result = _compile("block = extrude(rect(0, 0, 2, 2), -1.5)", record)
        self.assertEqual(_elements(result)["block-body"]["fields"]["params"], {
            "profile": [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]], "height": 1.5,
            "work_plane": {"origin": [0.0, 0.0, 0.0], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 0.0, 1.0],
                           "normal": [0.0, -1.0, 0.0]}})
        self.assertEqual(result.report[0]["bounds"], [[0.0, -1.5, 0.0], [2.0, 0.0, 2.0]])
        self.assertEqual(_view(_apply(record, result))["block"]["bounds"], [[0.0, -1.5, 0.0], [2.0, 0.0, 2.0]])

    def test_a_profile_drawn_on_a_plane_extrudes_along_its_normal(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "block_1 = extrude(rect(0, 0, 2, 3), 0.5, plane=front(z=1))",
            "block_2 = extrude(rect(0, 0, 2, 3), 0.5, plane=side(x=1))",
            "block_3 = extrude(rect(0, 0, 1, 1), 0.2, plane=plane((5, 1, 0), (1, 0, 0), (0, 1, 1)))",
        ]), record)
        first = _elements(result)["block-1-body"]["fields"]
        self.assertEqual(first["references"], {"base": {"level": "ground"}})
        self.assertEqual(first["params"], {
            "profile": [[0.0, 0.0], [2.0, 0.0], [2.0, 3.0], [0.0, 3.0]], "height": 0.5,
            "work_plane": {"origin": [0.0, 0.0, 1.0], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 1.0, 0.0],
                           "normal": [0.0, 0.0, 1.0]}})
        operations = _operations(_apply(record, result))

        def corners(op_id: str) -> set:
            params = _params(operations[op_id])
            base = [tuple(point) for point in params["profile"]]
            far = [tuple(c + v for c, v in zip(point, params["vector"])) for point in base]
            return {tuple(round(c, 6) for c in point) for point in base + far}

        self.assertEqual(corners("block-1-body"), {(x, y, z) for x in (0.0, 2.0) for y in (0.0, 3.0) for z in (1.0, 1.5)})
        self.assertEqual(corners("block-2-body"), {(x, y, z) for x in (1.0, 1.5) for y in (0.0, 3.0) for z in (0.0, 2.0)})
        s = math.sqrt(0.5)
        tilted = {(5.0 + u, round(1.0 + v * s - n * s, 6), round(v * s + n * s, 6))
                  for u in (0.0, 1.0) for v in (0.0, 1.0) for n in (0.0, 0.2)}
        self.assertEqual(corners("block-3-body"), {tuple(round(c, 6) for c in point) for point in tilted})
        report = {row["id"]: row for row in result.report}
        self.assertEqual(report["block-2"]["bounds"], [[1.0, 0.0, 0.0], [1.5, 3.0, 2.0]])

    def test_a_plane_axis_is_any_finite_direction_that_is_not_zero(self) -> None:
        record = _record()
        result = _compile("block = extrude(rect(0, 0, 1, 1), 0.5, plane=plane((0, 0, 0), (1e200, 0, 0), (0, 1e-3, 0)))",
                          record)
        drawing = _elements(result)["block-body"]["fields"]["params"]["work_plane"]
        self.assertEqual((drawing["xAxis"], drawing["yAxis"], drawing["normal"]),
                         ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]))
        _operations(_apply(record, result))
        self.refusals([
            ("x = 1\np = plane((0, 0, 0), (0, 0, 0), (0, 1, 0))", 2, "x_axis must not be zero"),
            ("x = 1\np = plane((0, 0, 0), (1, 0, 0), (0, 1e-300, 0))", 2, "y_axis must not be zero"),
            ("x = 1\np = plane((0, 0, 0), (1e400, 0, 0), (0, 1, 0))", 2, "finite"),
        ])

    def test_numbers_and_planes_are_measured_from_the_project_zero(self) -> None:
        record = _record(ground=1.0)
        result = _compile("p = extrude(rect(0, 0, 1, 1), 1, plane=front(z=0))\nb = extrude(rect(2, 0, 1, 1), 1, at=2)",
                          record)
        elements = _elements(result)
        self.assertEqual(elements["p-body"]["fields"]["params"]["work_plane"]["origin"], [0.0, -1.0, 0.0])
        self.assertEqual(elements["b-body"]["fields"]["params"]["elevation"], 1.0)
        view = _view(_apply(record, result))
        self.assertEqual(view["p"]["bounds"], [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        self.assertEqual(view["b"]["bounds"], [[2.0, 2.0, 0.0], [3.0, 3.0, 1.0]])
        self.assertEqual([row["bounds"] for row in result.report], [view["p"]["bounds"], view["b"]["bounds"]])

    def test_parameters_bind_a_height_and_an_offset(self) -> None:
        record = _record(parameters=PARAMETERS)
        result = _compile("\n".join([
            "c = extrude(rect(0, 0, 1, 1), param('h'), at=level('ground') + param('sill'))",
            "print(bounds(c))",
        ]), record)
        params = _elements(result)["c-body"]["fields"]["params"]
        self.assertEqual((params["height"], params["elevation"]), ("@h", "@sill"))
        self.assertEqual(result.log, ("((0.0, 0.9, 0.0), (1.0, 3.9, 1.0))",))
        _apply(record, result)

    def test_a_project_without_a_level_has_nothing_to_measure_from(self) -> None:
        record = _record(ground=None)
        self.refusals([
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1)", 2, "add a level first"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, at=2)", 2, "add a level first"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, plane=front())", 2, "add a level first"),
            ("x = 1\nf = face(rect(0, 0, 1, 1))", 2, "add a level first"),
            ("x = 1\np = path([(0, 0, 0), (1, 0, 0)])", 2, "add a level first"),
            ("x = 1\ns = section(rect(0, 0, 1, 1), 0)", 2, "add a level first"),
        ], record)


class OtherFormsLoweringTests(ConstructionTestCase):
    def test_face_path_and_loft_lower_to_rows_that_produce(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "face_1 = face(rect(0, 0, 4, 3), at=0.5)",
            "path_1 = path([(0, 1, 0), (4, 1, 0), (4, 1, 3)])",
            "path_2 = path([(0, 0, 0), (4, 1, 0)])",
            "loft_1 = loft([section(rect(0, 0, 2, 2), 0), section(rect(0.5, 0.5, 1, 1), 3)])",
            "loft_2 = loft([section(rect(0, 5, 2, 2), 0), section(rect(0, 5, 2, 2), 1)], cap=False)",
        ]), record)
        elements = _elements(result)
        self.assertEqual(elements["face-1-body"]["fields"], {
            "component_id": "face-1", "producer": "planar-surface", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0], [0.0, 0.0]], "elevation": 0.5}})
        self.assertEqual(elements["path-1-body"]["fields"], {
            "component_id": "path-1", "producer": "curve", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0]], "elevation": 1.0}})
        self.assertEqual(elements["path-2-body"]["fields"]["producer"], "curve")
        self.assertIn("work_plane", elements["path-2-body"]["fields"]["params"])
        self.assertEqual(elements["loft-1-body"]["fields"], {
            "component_id": "loft-1", "producer": "loft", "references": {"base": {"level": "ground"}},
            "params": {"profiles": [[[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 2.0], [0.0, 0.0, 2.0]],
                                    [[0.5, 3.0, 0.5], [1.5, 3.0, 0.5], [1.5, 3.0, 1.5], [0.5, 3.0, 1.5]]],
                       "profile_size": 4, "cap_ends": True}})
        self.assertIs(elements["loft-2-body"]["fields"]["params"]["cap_ends"], False)
        view = _view(_apply(record, result))
        self.assertEqual({key: row["form"] for key, row in view.items()},
                         {"face-1": "face", "path-1": "path", "path-2": "path", "loft-1": "solid", "loft-2": "other"})
        self.assertEqual(view["face-1"]["bounds"], [[0.0, 0.5, 0.0], [4.0, 0.5, 3.0]])
        self.assertEqual(view["path-2"]["bounds"], [[0.0, 0.0, 0.0], [4.0, 1.0, 0.0]])
        self.assertEqual(view["loft-1"]["bounds"], [[0.0, 0.0, 0.0], [2.0, 3.0, 2.0]])

    def test_loft_sections_on_one_level_are_one_base(self) -> None:
        record = _record(_level("upper", 3.0))
        result = _compile("\n".join([
            "p = rect(0, 0, 2, 2)",
            "q = rect(0.5, 0.5, 1, 1)",
            "loft_1 = loft([section(p, level('upper')), section(q, level('upper') + 3)])",
            "loft_2 = loft([section(p, 0), section(q, level('ground') + 3)])",
        ]), record)
        elements = _elements(result)
        for element_id, level in (("loft-1-body", "upper"), ("loft-2-body", "ground")):
            fields = elements[element_id]["fields"]
            self.assertEqual(fields["references"], {"base": {"level": level}})
            self.assertEqual([section[0][1] for section in fields["params"]["profiles"]], [0.0, 3.0])
        view = _view(_apply(record, result))
        self.assertEqual(view["loft-1"]["bounds"], [[0.0, 3.0, 0.0], [2.0, 6.0, 2.0]])
        self.assertEqual(view["loft-2"]["bounds"], [[0.0, 0.0, 0.0], [2.0, 3.0, 2.0]])
        error = self.refused("x = 1\nl = loft([section(rect(0, 0, 1, 1), level('upper')), section(rect(0, 0, 1, 1), 5)])",
                             record)
        self.assertEqual(error.line, 2)
        self.assertIn("same level", error.message)

    def test_a_path_that_leaves_its_plane_is_refused(self) -> None:
        error = self.refused("x = 1\np = path([(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)])")
        self.assertEqual(error.line, 2)
        self.assertIn("one plane", error.message)

    def test_profiles_are_checked_where_they_are_made(self) -> None:
        self.refusals([
            ("x = 1\np = polygon([(0, 0), (1, 0), (1, 1), (0, 0)])", 2, "a closed profile does not repeat its first point"),
            ("x = 1\np = polygon([(0, 0), (1, 0), (1, 0), (0, 1)])", 2, "repeat"),
            ("x = 1\np = polygon([(0, 0), (1, 0), (2, 0)])", 2, "area"),
            ("x = 1\np = polygon([(0, 0), (2, 2), (2, 0), (0, 1)])", 2, "crosses itself"),
            ("x = 1\np = circle(0, 0, 1, segments=4)", 2, "8"),
        ])

    def test_an_offset_that_collapses_the_profile_is_refused(self) -> None:
        self.refusals([
            ("x = 1\np = offset(rect(0, 0, 1, 1), -0.5)", 2, "offset(-0.5) collapses this profile"),
            ("x = 1\np = offset(rect(0, 0, 1, 1), -0.6)", 2, "offset(-0.6) collapses this profile"),
            ("x = 1\np = offset(rect(0, 0, 1, 1), -1)", 2, "offset(-1) collapses this profile"),
            ("x = 1\np = offset(rect(0, 0, 1, 1), -2)", 2, "offset(-2) collapses this profile"),
            ("x = 1\np = offset(rect(0, 0, 4, 1), -3)", 2, "offset(-3) collapses this profile"),
            ("x = 1\np = offset(polygon([(0, 0), (4, 0), (2, 3)]), -1.2)", 2, "offset(-1.2) collapses this profile"),
        ])

    def test_circle_and_offset_make_profiles(self) -> None:
        result = _compile("\n".join([
            "block_1 = extrude(circle(0, 0, 1, segments=8), 1)",
            "block_2 = extrude(offset(rect(0, 0, 2, 2), 0.5), 1)",
            "block_3 = extrude(offset(rect(0, 0, 2, 2), -0.5), 1)",
            "print(bounds(block_2), bounds(block_3))",
        ]))
        profile = _elements(result)["block-1-body"]["fields"]["params"]["profile"]
        self.assertEqual(profile[0], [1.0, 0.0])
        self.assertEqual(profile[2], [0.0, 1.0])  # counter-clockwise: +x turns toward +z
        self.assertEqual(result.log, ("((-0.5, 0.0, -0.5), (2.5, 1.0, 2.5)) ((0.5, 0.0, 0.5), (1.5, 1.0, 1.5))",))


class ReachTests(ConstructionTestCase):
    def test_coordinates_stay_within_reach_and_lengths_have_a_size(self) -> None:
        self.refusals([
            ("x = 1\np = rect(1e308, 0, 1e308, 1)", 2, "100 000 m"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, at=1e308)", 2, "100 000 m"),
            ("b = extrude(rect(0, 0, 1, 1), 1)\nfor i in range(3):\n    move(b, dx=60000)", 3, "100 000 m"),
            ("b = extrude(rect(0, 0, 1, 1), 1)\nmove(b, dx=1e308)", 2, "100 000 m"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1e-10)", 2, "0.000001 m"),
            ("x = 1\np = rect(0, 0, 1e-10, 1)", 2, "0.000001 m"),
            ("b = extrude(rect(0, 0, 1, 1), 1)\nscale(b, 1e-9)", 2, "0.000001 m"),
            ("x = 1\nc = circle(0, 0, 1e-9)", 2, "0.000001 m"),
        ])


class TransformTests(ConstructionTestCase):
    def test_rotation_turns_plus_x_toward_plus_z(self) -> None:
        result = _compile("a = extrude(polygon([(1, 0), (2, 0), (2, 1)]), 1)\nrotate(a, 90)")
        self.assertEqual(_elements(result)["a-body"]["fields"]["params"]["profile"], [[0.0, 1.0], [0.0, 2.0], [-1.0, 2.0]])
        result = _compile("a = extrude(rect(0, 0, 2, 1), 1)\nrotate(a, 180, about=(1, 0))")
        self.assertEqual(result.report[0]["bounds"], [[0.0, 0.0, -1.0], [2.0, 1.0, 0.0]])

    def test_move_mirror_and_scale_are_baked_into_the_definition(self) -> None:
        result = _compile("\n".join([
            "a = extrude(polygon([(1, 0), (2, 0), (2, 1)]), 1)",
            "mirror(a, x=0)",
            "b = extrude(rect(1, 1, 2, 2), 3, at=1)",
            "scale(b, 2, about=(1, 1))",
            "c = face(rect(0, 0, 1, 1))",
            "move(c, dx=1, dy=2, dz=3)",
        ]))
        elements = _elements(result)
        self.assertEqual(elements["a-body"]["fields"]["params"]["profile"], [[-1.0, 0.0], [-2.0, 0.0], [-2.0, 1.0]])
        self.assertEqual(elements["b-body"]["fields"]["params"],
                         {"profile": [[1.0, 1.0], [5.0, 1.0], [5.0, 5.0], [1.0, 5.0]], "height": 6.0, "elevation": 1.0})
        self.assertEqual(elements["c-body"]["fields"]["params"],
                         {"profile": [[1.0, 3.0], [2.0, 3.0], [2.0, 4.0], [1.0, 4.0], [1.0, 3.0]], "elevation": 2.0})

    def test_copy_and_array_make_new_shapes_without_the_cuts(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "block = extrude(rect(0, 0, 0.5, 0.5), 3)",
            "cut(block, extrude(rect(0.1, 0.1, 0.3, 0.3), 1, at=1))",
            "row = array(block, 3, dx=2)",
            "copy_1 = copy(block, dz=4)",
        ]), record)
        elements = _elements(result)
        cutter = _hashed("block-cut-", "cut ( block , extrude ( rect ( 0.1 , 0.1 , 0.3 , 0.3 ) , 1 , at = 1 ) )")
        self.assertEqual(sorted(elements), sorted(["block-body", f"{cutter}-body", "copy-1-body", "row-1-body",
                                                   "row-2-body"]))
        self.assertEqual(elements["block-body"]["fields"]["references"]["voids"], [f"{cutter}-body"])
        self.assertEqual(elements["row-2-body"]["fields"]["params"]["profile"][0], [4.0, 0.0])
        self.assertEqual(elements["row-1-body"]["fields"]["references"], {"base": {"level": "ground"}})
        self.assertEqual(elements["copy-1-body"]["fields"]["params"]["profile"][0], [0.0, 4.0])
        _apply(record, result)

    def test_a_parametric_offset_cannot_be_moved_vertically(self) -> None:
        error = self.refused("c = extrude(rect(0, 0, 1, 1), 1, at=level('ground') + param('sill'))\nmove(c, dy=0.1)",
                             _record(parameters=PARAMETERS))
        self.assertEqual(error.line, 2)


class IdentityTests(ConstructionTestCase):
    def test_loop_cutters_are_numbered_and_all_reach_the_host(self) -> None:
        record = _record()
        result = _compile(LOOP, record)
        self.assertEqual([row["id"] for row in result.report], ["mass", "w-1", "w-2", "w-3", "w-4"])
        host = _elements(result)["mass-body"]["fields"]
        self.assertEqual(host["references"]["voids"], CUTTERS)
        self.assertEqual(result.report[0]["cuts"], ["w-1", "w-2", "w-3", "w-4"])
        self.assertEqual(result.report[0]["line"], 4)
        self.assertEqual(result.summary, "construction: create mass, w-1..4; cut mass")
        operations = _operations(_apply(record, result))
        body, difference = operations["mass-body-body"], operations["mass-body"]
        self.assertEqual(body.output_object_ids, ("obj-mass-body-body",))
        self.assertEqual(difference.output_object_ids, ("obj-mass-body",))
        self.assertEqual(difference.input_object_ids, ("obj-mass-body-body", "obj-w-1-body", "obj-w-2-body",
                                                       "obj-w-3-body", "obj-w-4-body"))
        for index in range(1, 5):
            self.assertTrue(_params(operations[f"w-{index}-body"])["hidden_for_inspection"])
        delivered = delivered_object_ids(SimpleNamespace(operations=tuple(operations.values())))
        self.assertIn("obj-mass-body", delivered)
        self.assertNotIn("obj-mass-body-body", delivered)

    def test_running_the_same_script_again_updates_the_same_geometry(self) -> None:
        record = _record()
        first = _compile(LOOP, record)
        successor = _apply(record, first)
        second = _compile(LOOP, successor)
        self.assertEqual([row["schema"] for row in second.entities], ["Element@1"] * 5)
        self.assertEqual({row["status"] for row in second.report}, {"updated"})
        self.assertEqual(_elements(second), _elements(first))
        self.assertEqual(_apply(successor, second).entities, successor.entities)

    def test_a_script_that_cuts_existing_geometry_runs_again(self) -> None:
        record = _record()
        massed = _apply(record, _compile("mass = extrude(rect(0, 0, 12, 8), 3)", record))
        script = "\n".join(["m = get('mass')", "for i in range(4):",
                            "    w = extrude(rect(1 + 2.8 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)", "    cut(m, w)"])
        first = _compile(script, massed)
        successor = _apply(massed, first)
        second = _compile(script, successor)
        self.assertEqual([row["schema"] for row in second.entities], ["Element@1"] * 4)
        self.assertEqual({row["status"] for row in second.report}, {"updated"})
        self.assertEqual(_apply(successor, second).entities, successor.entities)

    def test_ids_come_from_names_then_variables_then_their_statements(self) -> None:
        result = _compile("\n".join([
            "mass = extrude(rect(0, 0, 10, 10), 2)",
            "Upper_Block = extrude(rect(1, 1, 2, 2), 1, at=top(mass))",
            "name(extrude(rect(5, 5, 1, 1), 1), 'named-1')",
            "extrude(rect(8, 8, 1, 1), 1)",
            "cut(mass, extrude(rect(3, 3, 1, 1), 1, at=0.5))",
            "extrude(rect(8, 0, 1, 1), 1)",
            "stack = [extrude(rect(i, 12, 0.5, 0.5), 1) for i in range(2)]",
            "for i in range(2):",
            "    extrude(rect(i, 20, 0.5, 0.5), 1)",
        ]))
        self.assertEqual([row["id"] for row in result.report], [
            "mass", "upper-block", "named-1",
            _hashed("shape-", "extrude ( rect ( 8 , 8 , 1 , 1 ) , 1 )"),
            _hashed("mass-cut-", "cut ( mass , extrude ( rect ( 3 , 3 , 1 , 1 ) , 1 , at = 0.5 ) )"),
            _hashed("shape-", "extrude ( rect ( 8 , 0 , 1 , 1 ) , 1 )"),
            "stack-1", "stack-2",
            _hashed("shape-", "extrude ( rect ( i , 20 , 0.5 , 0.5 ) , 1 )", index=0),
            _hashed("shape-", "extrude ( rect ( i , 20 , 0.5 , 0.5 ) , 1 )", index=1),
        ])

    def test_hashed_ids_have_twelve_digits_that_tell_statements_apart(self) -> None:
        first = _compile("extrude(rect(2862, 0, 1, 1), 1)")
        second = _compile("extrude(rect(4861, 0, 1, 1), 1)")
        one, two = first.report[0]["id"], second.report[0]["id"]
        self.assertNotEqual(one, two)  # six digits made these two the same shape
        for identifier in (one, two):
            self.assertRegex(identifier, r"^shape-[0-9a-f]{12}$")
        self.assertEqual(one, _hashed("shape-", "extrude ( rect ( 2862 , 0 , 1 , 1 ) , 1 )"))
        cut = _compile("m = extrude(rect(0, 0, 4, 4), 1)\ncut(m, extrude(rect(1, 1, 1, 1), 1, at=0.5))")
        self.assertRegex(cut.report[1]["id"], r"^m-cut-[0-9a-f]{12}$")

    def test_a_shape_made_in_a_helper_is_keyed_by_the_statements_that_called_it(self) -> None:
        helper = "def box(x):\n    return extrude(rect(x, 0, 1, 1), 1)\n"
        inner = "return extrude ( rect ( x , 0 , 1 , 1 ) , 1 )"
        one, two = _compile(helper + "box(2)"), _compile(helper + "box(5)")
        self.assertNotEqual(one.report[0]["id"], two.report[0]["id"])  # two scripts, two shapes
        self.assertEqual(one.report[0]["id"], _hashed("shape-", "box ( 2 )", inner))
        both = _compile(helper + "box(2)\nbox(5)")
        self.assertEqual([row["id"] for row in both.report], [one.report[0]["id"], two.report[0]["id"]])
        looped = _compile(helper + "for i in range(2):\n    box(i)")
        self.assertEqual([row["id"] for row in looped.report],
                         [_hashed("shape-", "box ( i )", inner, index=index) for index in range(2)])
        nested = _compile(helper + "def pair(x):\n    return [box(x), box(x + 1)]\npair(7)")
        self.assertEqual([row["id"] for row in nested.report],
                         [_hashed("shape-", "pair ( 7 )", "return [ box ( x ) , box ( x + 1 ) ]", inner, index=index)
                          for index in range(2)])
        record = _record()
        script = helper + "box(2)\nbox(5)"
        successor = _apply(record, _compile(script, record))
        again = _compile(script, successor)
        self.assertEqual([(row["id"], row["status"]) for row in again.report],
                         [(row["id"], "updated") for row in both.report])
        self.assertEqual([row["schema"] for row in again.entities], ["Element@1"] * 2)

    def test_an_unnamed_shape_keeps_its_id_while_its_statement_stays(self) -> None:
        record = _record()
        first = _compile("extrude(rect(0, 0, 1, 1), 1)", record)
        one = _hashed("shape-", "extrude ( rect ( 0 , 0 , 1 , 1 ) , 1 )")
        self.assertEqual([(row["id"], row["status"]) for row in first.report], [(one, "created")])
        successor = _apply(record, first)
        again = _compile("x = 0\nextrude( rect(0,0,1,1),\n    1 )  # the same statement, written differently", successor)
        self.assertEqual([(row["id"], row["status"]) for row in again.report], [(one, "updated")])
        other = _compile("extrude(rect(10, 10, 5, 5), 7)", successor)
        self.assertEqual([(row["id"], row["status"]) for row in other.report],
                         [(_hashed("shape-", "extrude ( rect ( 10 , 10 , 5 , 5 ) , 7 )"), "created")])

    def test_running_a_script_with_unnamed_shapes_three_times_updates_the_same_ids(self) -> None:
        script = "\n".join([
            "m = extrude(rect(0, 0, 10, 4), 3)",
            "extrude(rect(20, 0, 1, 1), 1)",
            "for i in range(3):",
            "    cut(m, extrude(rect(1 + 3 * i, -0.1, 1, 0.5), 1, at=1))",
        ])
        record = _record()
        first = _compile(script, record)
        ids = [row["id"] for row in first.report]
        self.assertEqual(ids[:2], ["m", _hashed("shape-", "extrude ( rect ( 20 , 0 , 1 , 1 ) , 1 )")])
        self.assertEqual(ids[2:], [_hashed("m-cut-", "cut ( m , extrude ( rect ( 1 + 3 * i , - 0.1 , 1 , 0.5 ) , 1 , "
                                                     "at = 1 ) )", index=index) for index in range(3)])
        self.assertEqual({row["status"] for row in first.report}, {"created"})
        successor = _apply(record, first)
        for _ in range(2):
            rerun = _compile(script, successor)
            self.assertEqual([row["id"] for row in rerun.report], ids)
            self.assertEqual({row["status"] for row in rerun.report}, {"updated"})
            self.assertEqual([row["schema"] for row in rerun.entities], ["Element@1"] * 5)
            following = _apply(successor, rerun)
            self.assertEqual(following.entities, successor.entities)
            successor = following

    def test_an_unnamed_shape_whose_id_names_something_else_is_refused(self) -> None:
        taken = _hashed("shape-", "extrude ( rect ( 0 , 0 , 1 , 1 ) , 1 )")
        error = self.refused("x = 1\nextrude(rect(0, 0, 1, 1), 1)", _record(_level(taken, 5.0)))
        self.assertEqual(error.line, 2)
        self.assertEqual(error.message, f"{taken} is already Level@1 in this project; name the new shape differently "
                                        "or get() it")

    def test_a_name_already_used_in_the_project_is_refused(self) -> None:
        error = self.refused("a = extrude(rect(0, 0, 1, 1), 1)\nname(a, 'ground')")
        self.assertEqual(error.line, 2)
        self.assertEqual(error.message, "ground is already Level@1 in this project; name the new shape differently or get() it")
        error = self.refused("model = extrude(rect(0, 0, 1, 1), 1)")
        self.assertEqual(error.line, 1)
        self.assertEqual(error.message, "model is already Component@1 in this project; name the new shape differently or get() it")
        error = self.refused("a = extrude(rect(0, 0, 1, 1), 1)\nb = 2\nname(a, 'model')")
        self.assertEqual(error.line, 3)
        self.assertIn("model is already Component@1", error.message)

    def test_a_name_used_by_other_geometry_is_refused(self) -> None:
        record = _record(
            Entity("pair", "Component@1", {"intent": "pair"}, parent_id="model"),
            Entity("pair-a", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": 1}}),
            Entity("pair-b", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[2, 0], [3, 0], [3, 1], [2, 1]], "height": 2}}),
            Entity("single", "Component@1", {"intent": "single"}, parent_id="model"),
            Entity("single-line", "Element@1", {"component_id": "single", "producer": "prism",
                                                "references": {"base": {"level": "ground"}},
                                                "params": {"profile": [[5, 0], [6, 0], [6, 1], [5, 1]], "height": 1}}),
        )
        self.refusals([
            ("x = 1\npair = extrude(rect(0, 0, 1, 1), 1)", 2, "pair is already Component@1"),
            ("x = 1\na = name(extrude(rect(0, 0, 1, 1), 1), 'pair-a')", 2, "pair-a is already Element@1"),
            ("x = 1\nsingle = extrude(rect(0, 0, 1, 1), 1)", 2, "single is already Component@1"),
            ("x = 1\nsingle_line = extrude(rect(0, 0, 1, 1), 1)", 2, "single-line is already Element@1"),
            ("x = 1\na = name(extrude(rect(0, 0, 1, 1), 1), 'ground-body')", 2, "-body"),
        ], record)

    def test_construction_made_geometry_is_a_component_whose_one_element_is_its_body(self) -> None:
        # The one test of what a script made (#419 C7 round 2): lowering reuses such a component, and a keep
        # on the component a shape is placed under does not reach it.
        from monkeyarch.construction import made_by_construction

        self.assertTrue(made_by_construction("mass", ["mass" + ELEMENT_SUFFIX]))
        for component, elements in (("pair", ["pair-a", "pair-b"]), ("single", ["single-line"]), ("model", []),
                                    ("model", None), ("mass", ["mass-body", "mass-body-2"])):
            with self.subTest(component=component, elements=elements):
                self.assertFalse(made_by_construction(component, elements))

    def test_two_shapes_with_one_id_are_refused_at_the_second(self) -> None:
        error = self.refused("a = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(2, 0, 1, 1), 1)\nname(b, 'a')")
        self.assertEqual(error.line, 3)

    def test_an_id_a_variable_cannot_spell_asks_for_a_name(self) -> None:
        error = self.refused("x = 1\n_tmp = extrude(rect(0, 0, 1, 1), 1)")
        self.assertEqual(error.line, 2)
        self.assertIn("name(", error.message)
        self.assertEqual(self.refused("a = name(extrude(rect(0, 0, 1, 1), 1), 'mass-body')").line, 1)


class RedefinitionTests(ConstructionTestCase):
    def test_redefining_a_cut_shape_keeps_its_cuts(self) -> None:
        record = self.cut_record()
        result = _compile("mass = extrude(rect(0, 0, 12, 8), 4)", record)
        self.assertEqual(_elements(result)["mass-body"]["fields"]["references"],
                         {"base": {"level": "ground"}, "voids": CUTTERS})
        self.assertEqual(result.report[0]["cuts"], ["w-1", "w-2", "w-3", "w-4"])
        self.assertEqual(result.summary, "construction: update mass")
        view = _view(_apply(record, result))
        self.assertEqual((view["mass"]["cuts"], view["w-1"]["hidden"]), (["w-1", "w-2", "w-3", "w-4"], True))

    def test_uncut_removes_one_or_every_cutter(self) -> None:
        record = self.cut_record()
        one = _compile("mass = extrude(rect(0, 0, 12, 8), 4)\nuncut(mass, get('w-2'))", record)
        self.assertEqual(_elements(one)["mass-body"]["fields"]["references"]["voids"],
                         ["w-1-body", "w-3-body", "w-4-body"])
        self.assertEqual(one.summary, "construction: update mass; uncut mass")
        every = _compile("mass = extrude(rect(0, 0, 12, 8), 4)\nuncut(mass)", record)
        self.assertEqual(_elements(every)["mass-body"]["fields"]["references"], {"base": {"level": "ground"}})
        existing = _compile("uncut(get('mass'))", record)
        self.assertEqual(existing.entities[0]["fields"]["references"], {"base": {"level": "ground"}})
        self.assertEqual(_view(_apply(record, existing))["w-1"]["hidden"], False)
        self.refusals([("mass = extrude(rect(0, 0, 12, 8), 4)\nc = extrude(rect(20, 0, 1, 1), 1)\nuncut(mass, c)",
                        3, "c does not cut mass")], record)

    def test_a_cut_shape_stays_a_solid_and_cannot_cut(self) -> None:
        self.refusals([
            ("x = 1\nmass = face(rect(0, 0, 12, 8))", 2, "mass has cuts; it must stay a solid"),
            ("x = 1\nmass = path([(0, 0, 0), (1, 0, 0)])", 2, "mass has cuts; it must stay a solid"),
            ("mass = extrude(rect(0, 0, 12, 8), 4)\nother = extrude(rect(-5, -5, 30, 30), 5)\ncut(other, mass)",
             3, "mass has cutters of its own"),
        ], self.cut_record())

    def test_a_host_can_hold_three_hundred_cutters(self) -> None:
        record = _record()
        many = _apply(record, _compile("\n".join([
            "mass = extrude(rect(0, 0, 400, 2), 3)",
            "cs = [extrude(rect(i + 0.1, 0.5, 0.5, 0.5), 1, at=1) for i in range(250)]",
            "cut(mass, cs)",
        ]), record))
        self.refusals([("m = get('mass')\nmore = [extrude(rect(i + 0.3, 0.5, 0.2, 0.2), 1, at=1) for i in range(60)]\n"
                        "cut(m, more)", 3, "300 cutters"),
                       ("mass = extrude(rect(0, 0, 400, 2), 3)\n"
                        "more = [extrude(rect(i + 0.3, 0.5, 0.2, 0.2), 1, at=1) for i in range(60)]\ncut(mass, more)",
                        3, "300 cutters")], many)


class RecordRelationTests(ConstructionTestCase):
    def stacked(self) -> StateRecord:
        record = _record()
        return _apply(record, _compile("a = extrude(rect(0, 0, 4, 3), 3)\nb = extrude(rect(1, 1, 1, 1), 1, at=top(a))",
                                       record))

    def test_a_cutter_of_the_record_stays_a_solid_without_cutters(self) -> None:
        self.refusals([
            ("x = 1\nw_1 = face(rect(1, -0.2, 1.2, 0.6))", 2, "w-1 cuts mass; it must stay a solid"),
            ("w_1 = extrude(rect(1, -0.2, 1.2, 0.6), 1.5, at=0.9)\nc = extrude(rect(1.2, -0.1, 0.2, 0.2), 0.2, at=1)\n"
             "cut(w_1, c)", 3, "w-1 has cutters of its own and cannot cut mass"),
            ("x = 1\nb = extrude(rect(1, 0, 1, 1), 1, at=top(get('w-1')))", 2, "cutter"),
        ], self.cut_record())

    def test_what_carries_another_shape_stays_a_solid_extruded_upward(self) -> None:
        self.refusals([
            ("x = 1\na = extrude(rect(0, 0, 4, 3), -3)", 2, "b stands on the top of a; a must stay a solid extruded upward"),
            ("x = 1\na = face(rect(0, 0, 4, 3))", 2, "b stands on the top of a"),
            ("x = 1\nc = extrude(rect(0, 0, 9, 9), 5)\ncut(c, get('a'))", 3, "stands on the top of a"),
        ], self.stacked())

    def test_a_shape_never_stands_on_its_own_top_through_the_record(self) -> None:
        self.refusals([
            ("x = 1\nset_base(get('a'), top(get('b')))", 2, "a would stand on its own top"),
            ("x = 1\na = extrude(rect(0, 0, 4, 3), 3, at=top(get('b')))", 2, "a would stand on its own top"),
        ], self.stacked())

    def test_the_record_relations_are_checked_as_the_script_leaves_them(self) -> None:
        record = self.cut_record()
        redefined = "mass = extrude(rect(0, 0, 12, 8), 3)\n"
        freed = {  # mass, redefined, no longer cuts w-1: w-1 is free geometry again
            "delete a former cutter": redefined + "uncut(mass)\ndelete(get('w-1'))",
            "stand on a former cutter": redefined + "uncut(mass, get('w-1'))\n"
                                                    "k = extrude(rect(1, 0, 1, 1), 1, at=top(get('w-1')))",
            "cut into a former cutter": redefined + "uncut(mass)\nd = extrude(rect(1.1, -0.3, 0.2, 0.2), 1, at=1)\n"
                                                    "cut(get('w-1'), d)",
        }
        for title, script in freed.items():
            with self.subTest(title):
                successor = _apply(record, _compile(script, record))
                self.assertEqual(_view(successor)["mass"]["cuts"], [] if "uncut(mass)\n" in script else
                                 ["w-2", "w-3", "w-4"])
        self.refusals([  # mass, in the record or redefined, still cuts w-1
            ("x = 1\ndelete(get('w-1'))", 2, "w-1 still cuts mass; uncut it first"),
            (redefined + "x = 1\ndelete(get('w-1'))", 3, "w-1 still cuts mass; uncut it first"),
            (redefined + "k = extrude(rect(1, 0, 1, 1), 1, at=top(get('w-1')))", 2,
             "k stands on the top of w-1, which cuts mass; a cutter carries nothing"),
            ("d = extrude(rect(1.1, -0.3, 0.2, 0.2), 1, at=1)\ncut(get('w-1'), d)", 2,
             "w-1 has cutters of its own and cannot cut mass"),
        ], record)
        stacked = self.stacked()
        _apply(stacked, _compile("b = extrude(rect(1, 1, 1, 1), 1)\ndelete(get('a'))", stacked))  # b stands elsewhere
        self.refusals([("x = 1\ndelete(get('a'))", 2, "b stands on the top of a; delete or move it first")], stacked)


class ExistingGeometryTests(ConstructionTestCase):
    def _massed(self) -> StateRecord:
        record = _record()
        return _apply(record, _compile("mass = extrude(rect(0, 0, 4, 3), 3)", record))

    def test_get_and_move_rewrite_the_same_element(self) -> None:
        record = self._massed()
        result = _compile("m = get('mass')\nmove(m, dx=1, dz=2)", record)
        self.assertEqual(result.entities, ({
            "entity_id": "mass-body", "schema": "Element@1", "parent_id": "mass", "fields": {
                "params": {"profile": [[1.0, 2.0], [5.0, 2.0], [5.0, 5.0], [1.0, 5.0]], "height": 3.0},
                "references": {"base": {"level": "ground"}}}},))
        self.assertEqual(result.report, ({"id": "mass", "form": "solid", "status": "updated",
                                          "bounds": [[1.0, 0.0, 2.0], [5.0, 3.0, 5.0]], "cuts": [], "line": 2},))
        successor = _apply(record, result)
        self.assertEqual(_view(successor)["mass"]["bounds"], [[1.0, 0.0, 2.0], [5.0, 3.0, 5.0]])
        result = _compile("move(get('mass-body'), dy=1)", record)
        self.assertEqual(result.entities[0]["fields"]["params"]["elevation"], 1.0)

    def test_existing_geometry_can_be_cut_by_new_geometry(self) -> None:
        record = self._massed()
        result = _compile("m = get('mass')\ncutter = extrude(rect(1, -0.1, 1, 0.5), 1, at=1)\ncut(m, cutter)", record)
        self.assertEqual(_elements(result)["mass-body"]["fields"]["references"],
                         {"base": {"level": "ground"}, "voids": ["cutter-body"]})
        successor = _apply(record, result)
        self.assertEqual(_view(successor)["cutter"]["cutBy"], ["mass"])
        uncut = _compile("uncut(get('mass'), get('cutter'))", successor)
        self.assertEqual(uncut.entities[0]["fields"]["references"], {"base": {"level": "ground"}})
        self.assertEqual(uncut.summary, "construction: uncut mass")

    def test_a_copy_and_a_new_base_of_existing_geometry_are_lowered_and_produce(self) -> None:
        record = self._massed()
        result = _compile("\n".join([
            "copy_1 = copy(get('mass'), dx=10)",
            "block = extrude(rect(20, 0, 5, 5), 2)",
            "set_base(get('mass'), top(block))",
            "print(bounds(get('mass')))",
        ]), record)
        elements = _elements(result)
        self.assertEqual(elements["copy-1-body"]["fields"], {
            "component_id": "copy-1", "producer": "prism", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[10.0, 0.0], [14.0, 0.0], [14.0, 3.0], [10.0, 3.0]], "height": 3.0}})
        self.assertEqual(elements["mass-body"]["fields"]["references"], {"base": {"datum": "block-body-top"}})
        self.assertEqual(result.log, ("((0.0, 2.0, 0.0), (4.0, 5.0, 3.0))",))
        self.assertEqual(_view(_apply(record, result))["mass"]["bounds"], [[0.0, 2.0, 0.0], [4.0, 5.0, 3.0]])

    def test_edits_of_existing_geometry_refuse_at_their_line(self) -> None:
        record = self._massed()
        cut = _apply(record, _compile("cutter = extrude(rect(1, -0.1, 1, 0.5), 1, at=1)\ncut(get('mass'), cutter)", record))
        error = self.refused("x = 1\ndelete(get('cutter'))", cut)
        self.assertEqual(error.line, 2)
        self.assertIn("still cuts mass", error.message)
        bound = _record(Entity("block-2", "Component@1", {"intent": "block-2"}, parent_id="model"),
                        Entity("block-2-body", "Element@1", {"component_id": "block-2", "producer": "prism",
                                                             "references": {"base": {"level": "ground"}},
                                                             "params": {"profile": [[0, 0], ["@h", 0], ["@h", 1], [0, 1]],
                                                                        "height": 1}}, parent_id="block-2"),
                        parameters=PARAMETERS)
        error = self.refused("b = get('block-2')\nmove(b, dx=1)", bound)
        self.assertEqual(error.line, 2)
        self.assertIn("bound to project parameters", error.message)
        self.assertEqual(_compile("print(bounds(get('block-2')))", bound).log, ("((0.0, 0.0, 0.0), (3.0, 1.0, 1.0))",))

    def test_delete_removes_the_component_and_its_element(self) -> None:
        record = self._massed()
        result = _compile("delete(get('mass'))", record)
        self.assertEqual((result.entities, result.remove_entity_ids), ((), ("mass", "mass-body")))
        self.assertEqual(result.report, ({"id": "mass", "form": "solid", "status": "deleted", "bounds": None,
                                          "cuts": [], "line": 1},))
        self.assertEqual(result.summary, "construction: delete mass")
        self.assertNotIn("mass", {entity.entity_id for entity in _apply(record, result).entities})

    def test_a_new_shape_deleted_in_the_same_script_is_dropped(self) -> None:
        result = _compile("a = extrude(rect(0, 0, 1, 1), 1)\ndelete(a)")
        self.assertEqual((result.entities, result.report), ((), ()))

    def test_get_refuses_what_is_not_editable_geometry(self) -> None:
        for script, words in (("get('model')", "model"), ("get('ground')", "Level@1"), ("get('nothing')", "nothing")):
            with self.subTest(script=script):
                error = self.refused("x = 1\n" + script, self._massed())
                self.assertEqual(error.line, 2)
                self.assertIn(words, error.message)

    def test_a_line_placed_block_moves_rotates_and_mirrors_by_its_points(self) -> None:
        block = (Entity("block-1", "Component@1", {"intent": "block-1"}, parent_id="model"),
                 Entity("block-1-line", "Element@1", {
                     "component_id": "block-1", "producer": "wall",
                     "references": {"base": {"level": "ground"},
                                    "line": {"from": {"point": [0.0, 0.0]}, "to": {"point": [4.0, 0.0]}}},
                     "params": {"thickness": 0.3, "height": 3.0}}, parent_id="block-1"))
        record = _record(*block)
        result = _compile("b = get('block-1')\nmove(b, dx=1)\nrotate(b, 90)", record)
        self.assertEqual(result.entities, ({
            "entity_id": "block-1-line", "schema": "Element@1", "parent_id": "block-1", "fields": {
                "params": {"thickness": 0.3, "height": 3.0},
                "references": {"base": {"level": "ground"},
                               "line": {"from": {"point": [0.0, 1.0]}, "to": {"point": [0.0, 5.0]}}}}},))
        _apply(record, result)
        mirrored = _apply(record, _compile("mirror(get('block-1'), z=1)", record))
        self.assertEqual(_view(record)["block-1"]["bounds"], [[0.0, 0.0, -0.3], [4.0, 3.0, 0.0]])
        self.assertEqual(_view(mirrored)["block-1"]["bounds"], [[0.0, 0.0, 2.0], [4.0, 3.0, 2.3]])
        error = self.refused("b = get('block-1')\nmove(b, dy=1)", record)
        self.assertEqual(error.line, 2)
        error = self.refused("b = get('block-1')\nc = copy(b, dx=1)", record)
        self.assertEqual(error.line, 2)

    def test_geometry_realized_with_openings_support_is_changed_only_with_get(self) -> None:
        """A block re-realized with support for doors and windows keeps its element id; a new definition under its
        id would turn it back into a plain solid and drop its openings, so it is refused at the line."""

        opening = {"opening_id": "window-1", "kind": "window", "along": 0.8, "width": 1.2, "sill": 0.9, "head": 2.1}
        for openings in ([opening], None):
            params = {"thickness": 0.3, "height": 3.0}
            if openings is not None:
                params["openings"] = openings
            record = _record(
                Entity("block-1", "Component@1", {"intent": "block-1"}, parent_id="model"),
                Entity("block-1-body", "Element@1", {
                    "component_id": "block-1", "producer": "wall", "params": params,
                    "references": {"base": {"level": "ground"},
                                   "line": {"from": {"point": [0.0, 0.0]}, "to": {"point": [4.0, 0.0]}}}},
                    parent_id="block-1"))
            with self.subTest(openings=openings):
                self.refusals([
                    ("x = 1\nblock_1 = extrude(rect(0, 0, 4, 0.3), 3)", 2, "block-1 is realized with support for openings"),
                    ("b = extrude(rect(0, 0, 4, 0.3), 3)\nx = 1\nname(b, 'block-1')", 3, "change it with get()"),
                ], record)
                result = _compile("b = get('block-1')\nmove(b, dx=1)\nset_height(b, 4)", record)
                self.assertEqual(result.entities[0]["entity_id"], "block-1-body")
                self.assertNotIn("producer", result.entities[0]["fields"])
                self.assertEqual(result.entities[0]["fields"]["params"].get("openings"), openings)
                self.assertEqual(result.entities[0]["fields"]["params"]["height"], 4.0)

    def test_a_block_on_the_project_grid_is_not_moved_by_a_script(self) -> None:
        record = _record(
            Entity("axis-a", "GridAxis@1", {"role": "A", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}),
            Entity("block-1", "Component@1", {"intent": "block-1"}, parent_id="model"),
            Entity("block-1-line", "Element@1", {
                "component_id": "block-1", "producer": "wall",
                "references": {"base": {"level": "ground"},
                               "line": {"from": {"axis_point": {"axis": "A", "along": 0}},
                                        "to": {"axis_point": {"axis": "A", "along": 4}}}},
                "params": {"thickness": 0.3, "height": 3.0}}, parent_id="block-1"),
        )
        error = self.refused("b = get('block-1')\nmove(b, dx=1)", record)
        self.assertEqual(error.line, 2)
        self.assertIn("grid", error.message)


class CutRefusalTests(ConstructionTestCase):
    def test_cut_refusals_name_their_line(self) -> None:
        solid = "extrude(rect({x}, 0, 1, 1), 1)"
        self.refusals([
            ("f = face(rect(0, 0, 1, 1))\nc = " + solid.format(x=0) + "\ncut(f, c)", 3, "solid"),
            ("m = " + solid.format(x=0) + "\np = path([(0, 0, 0), (1, 0, 0)])\ncut(m, p)", 3, "solid"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\nd = " + solid.format(x=4)
             + "\ncut(c, d)\ncut(m, c)", 5, "cutters of its own"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\nd = " + solid.format(x=4)
             + "\ncut(m, c)\ncut(c, d)", 5, "is itself a cutter"),
            ("m = " + solid.format(x=0) + "\ncut(m, m)", 2, "itself"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\ncut(m, c)\nb = extrude(rect(0, 0, 1, 1), 1, at=top(c))",
             4, "cutter"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\nb = extrude(rect(0, 0, 1, 1), 1, at=top(c))\ncut(m, c)",
             4, "stands on"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\nuncut(m, c)", 3, "does not cut"),
            ("m = " + solid.format(x=0) + "\nc = " + solid.format(x=2) + "\nt = top(c)\ncut(m, c)"
             + "\nb = extrude(rect(0, 0, 1, 1), 1, at=t)", 5, "cutter"),
        ])


class AnchorRefusalTests(ConstructionTestCase):
    def test_anchor_refusals_name_their_line(self) -> None:
        self.refusals([
            ("f = face(rect(0, 0, 1, 1))\nb = extrude(rect(0, 0, 1, 1), 1, at=top(f))", 2, "top()"),
            ("p = extrude(rect(0, 0, 1, 1), -1)\nb = extrude(rect(0, 0, 1, 1), 1, at=top(p))", 2, "top()"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, at=1, plane=front())", 2, "plane"),
            ("x = 1\nh = param('h') * 2", 2, "param('h') is a binding; use it directly or define the expression in the project's parameters"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, at=level('roof'))", 2, "roof"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), param('depth'))", 2, "depth"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 1, at=level('ground') * 2)", 2, "anchor"),
            ("a = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(0, 0, 1, 1), 1, at=top(a))\ndelete(a)", 3, "stands on"),
            ("x = 1\nb = extrude(rect(0, 0, 1, 1), 0)", 2, "face()"),
            ("x = 1\np = plane((0, 0, 0), (1, 0, 0), (1, 1, 0))", 2, "perpendicular"),
            ("x = 1\nif level('ground') == 0:\n    pass", 2, "compared"),
            ("x = 1\nb = rect('a', 0, 1, 1)", 2, "number"),
            ("x = 1\ns = loft([section(rect(0, 0, 1, 1), 0), section(rect(0, 0, 1, 1), level('ground') + param('h'))])",
             2, "param('h')"),
        ], _record(parameters=PARAMETERS))


class SupportChangeTests(ConstructionTestCase):
    """#419 round 4: a top the script reads resolves through the script's model, and the report states what the
    successor record produces."""

    def stacked(self) -> StateRecord:
        record = _record()
        return _apply(record, _compile("a = extrude(rect(0, 0, 4, 3), 3)\nb = extrude(rect(1, 1, 1, 1), 1, at=top(a))",
                                       record))

    def test_bounds_follow_a_support_the_script_changed(self) -> None:
        record = self.stacked()
        cases = {
            "set_height(get('a'), 5)\nprint(bounds(get('b')))": [[1.0, 5.0, 1.0], [2.0, 6.0, 2.0]],
            "move(get('a'), dy=2)\nprint(bounds(get('b')))": [[1.0, 5.0, 1.0], [2.0, 6.0, 2.0]],
            "c = extrude(rect(10, 10, 1, 1), 10)\nset_base(get('a'), top(c))\nprint(bounds(get('b')))":
                [[1.0, 13.0, 1.0], [2.0, 14.0, 2.0]],
            "b = get('b')\nset_height(get('a'), 5)\nprint(bounds(b))": [[1.0, 5.0, 1.0], [2.0, 6.0, 2.0]],
        }
        for script, expected in cases.items():
            with self.subTest(script=script):
                result = _compile(script, record)
                view = _view(_apply(record, result))
                self.assertEqual(view["b"]["bounds"], expected)
                self.assertEqual(result.log, (str((tuple(expected[0]), tuple(expected[1]))),))
                for row in result.report:
                    self.assertEqual(row["bounds"], view[row["id"]]["bounds"], row["id"])

    def test_bounds_follow_a_support_the_script_redefined(self) -> None:
        record = self.stacked()
        result = _compile("a = extrude(rect(0, 0, 4, 3), 5)\nmove(get('b'), dx=1)\nprint(bounds(get('b')))", record)
        view = _view(_apply(record, result))
        report = {row["id"]: row for row in result.report}
        self.assertEqual(view["b"]["bounds"], [[2.0, 5.0, 1.0], [3.0, 6.0, 2.0]])
        self.assertEqual(report["b"]["bounds"], view["b"]["bounds"])
        self.assertEqual(result.log, ("((2.0, 5.0, 1.0), (3.0, 6.0, 2.0))",))
        # A top measured before the redefinition is measured anew after it: naming a shape changes the model too.
        again = _compile("print(bounds(get('b')))\na = extrude(rect(0, 0, 4, 3), 5)\nprint(bounds(get('b')))\n"
                         "name(extrude(rect(0, 0, 4, 3), 7), 'a')\ndelete(a)\nprint(bounds(get('b')))", record)
        self.assertEqual(again.log, ("((1.0, 3.0, 1.0), (2.0, 4.0, 2.0))", "((1.0, 5.0, 1.0), (2.0, 6.0, 2.0))",
                                     "((1.0, 7.0, 1.0), (2.0, 8.0, 2.0))"))

    def test_a_new_shape_on_a_carried_top_is_reported_where_it_is_produced(self) -> None:
        record = self.stacked()
        for support in ("set_height(get('a'), 5)", "a = extrude(rect(0, 0, 4, 3), 5)"):
            with self.subTest(support=support):
                result = _compile(support + "\nk = extrude(rect(1, 1, 1, 1), 1, at=top(get('b')))\nprint(bounds(k))",
                                  record)
                view = _view(_apply(record, result))
                report = {row["id"]: row for row in result.report}
                self.assertEqual(view["k"]["bounds"], [[1.0, 6.0, 1.0], [2.0, 7.0, 2.0]])
                self.assertEqual(report["k"]["bounds"], view["k"]["bounds"])
                self.assertEqual(result.log, ("((1.0, 6.0, 1.0), (2.0, 7.0, 2.0))",))

    def test_the_report_states_what_the_successor_produces(self) -> None:
        record = _record()
        result = _compile(LOOP + "\nblock = extrude(rect(2, 2, 2, 2), 1, at=top(mass))\nprint(bounds(mass))", record)
        view = _view(_apply(record, result))
        self.assertEqual([row["bounds"] for row in result.report], [view[row["id"]]["bounds"] for row in result.report])
        self.assertEqual(view["block"]["bounds"], [[2.0, 3.0, 2.0], [4.0, 4.0, 4.0]])
        multi = _record(
            Entity("pair", "Component@1", {"intent": "pair"}, parent_id="model"),
            Entity("pair-a", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": 1}}),
            Entity("pair-b", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[2, 0], [3, 0], [3, 1], [2, 1]], "height": 2}}),
        )
        result = _compile("move(get('pair-b'), dx=1)", multi)
        self.assertEqual(result.report[0]["bounds"], [[3.0, 0.0, 0.0], [4.0, 2.0, 1.0]])  # the part, not the pair

    def chained(self) -> StateRecord:
        record = _record()
        return _apply(record, _compile("\n".join([
            "a = extrude(rect(0, 0, 4, 3), 3)", "b = extrude(rect(1, 1, 2, 1), 1, at=top(a))",
            "c = extrude(rect(1, 1, 1, 1), 1, at=top(b))"]), record))

    def test_bounds_follow_a_chain_through_elements_the_script_never_reached(self) -> None:
        """Round 5: c stands on b on a; the script touches a only, and reads c (or stands on it)."""

        record = self.chained()
        cases = {
            "set_height(get('a'), 5)\nprint(bounds(get('c')))": ("c", [[1.0, 6.0, 1.0], [2.0, 7.0, 2.0]]),
            "move(get('a'), dy=1)\nprint(bounds(get('c')))": ("c", [[1.0, 5.0, 1.0], [2.0, 6.0, 2.0]]),
            "a = extrude(rect(0, 0, 4, 3), 5)\nprint(bounds(get('c')))": ("c", [[1.0, 6.0, 1.0], [2.0, 7.0, 2.0]]),
            "set_height(get('a'), 5)\nk = extrude(rect(1, 1, 1, 1), 1, at=top(get('c')))\nprint(bounds(k))":
                ("k", [[1.0, 7.0, 1.0], [2.0, 8.0, 2.0]]),
            "set_height(get('a'), 5)\nk = extrude(rect(1, 1, 1, 1), 1, at=top(get('c')) + 1)\nprint(bounds(k))":
                ("k", [[1.0, 8.0, 1.0], [2.0, 9.0, 2.0]]),
        }
        for script, (measured, expected) in cases.items():
            with self.subTest(script=script):
                result = _compile(script, record)
                view = _view(_apply(record, result))
                self.assertEqual(view[measured]["bounds"], expected)
                self.assertEqual(result.log, (str((tuple(expected[0]), tuple(expected[1]))),))
                for row in result.report:
                    self.assertEqual(row["bounds"], view[row["id"]]["bounds"], row["id"])

    def test_a_deep_stack_of_supports_is_measured_without_recursion(self) -> None:
        """Round 5: 250 links, the bottom changed, the top measured - with every link loaded, and with none."""

        record = _record()
        stacked = _apply(record, _compile("b = extrude(rect(0, 0, 1, 1), 1)\nfor i in range(249):\n"
                                          "    b = extrude(rect(0, 0, 1, 1), 1, at=top(b))", record))
        loaded = "\n".join(f"get('b-{index}')" for index in range(2, 250))
        for script in ("set_height(get('b-1'), 2)\nprint(bounds(get('b-250')))",
                       loaded + "\nset_height(get('b-1'), 2)\nprint(bounds(get('b-250')))"):
            with self.subTest(loaded=script.count("get(") > 2):
                result = _compile(script, stacked)
                self.assertEqual(result.log, ("((0.0, 250.0, 0.0), (1.0, 251.0, 1.0))",))
                self.assertEqual(_view(_apply(stacked, result))["b-250"]["bounds"], [[0.0, 250.0, 0.0], [1.0, 251.0, 1.0]])

    def test_a_shape_measured_while_it_would_stand_on_its_own_top_is_refused(self) -> None:
        record = self.stacked()
        for script in ("a = extrude(rect(0, 0, 4, 3), 3, at=top(get('b')))\nprint(bounds(a))",
                       "a = extrude(rect(0, 0, 4, 3), 3, at=top(get('b')))\nprint(bounds(get('b')))"):
            with self.subTest(script=script):
                error = self.refused(script, record)
                self.assertEqual(error.line, 2)
                self.assertIn("own top", error.message)


class ReportScaleTests(ConstructionTestCase):
    """Round 5: the report is produced for the shapes the script leaves and what they mention, never the project."""

    def test_a_one_line_script_on_a_large_project_reports_in_bounded_time(self) -> None:
        entities = []
        for index in range(3000):
            x, z = (index % 60) * 2.0, (index // 60) * 2.0
            entities.append(Entity(f"box-{index}", "Component@1", {"intent": f"box-{index}"}, parent_id="model"))
            entities.append(Entity(f"box-{index}-body", "Element@1", {
                "component_id": f"box-{index}", "producer": "prism", "references": {"base": {"level": "ground"}},
                "params": {"profile": [[x, z], [x + 1, z], [x + 1, z + 1], [x, z + 1]], "height": 3.0}},
                parent_id=f"box-{index}"))
        record = _record(*entities)
        started = time.perf_counter()
        result = _compile("extrude(rect(-50, -50, 1, 1), 1)\nk = extrude(rect(0, 0, 1, 1), 1, at=top(get('box-0')))",
                          record)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5, f"{elapsed:.2f}s for two shapes on 3 000 boxes")
        report = {row["id"]: row["bounds"] for row in result.report}
        self.assertEqual(report["k"], [[0.0, 3.0, 0.0], [1.0, 4.0, 1.0]])  # box-0's top: produced with k, not the project
        self.assertEqual(sorted(report.values()), sorted([[[-50.0, 0.0, -50.0], [-49.0, 1.0, -49.0]], report["k"]]))

    def test_the_report_follows_every_element_a_row_mentions(self) -> None:
        """A row placed on another element's line (a host, not a top) is produced with that element for its bounds."""

        record = _record(
            Entity("block-1", "Component@1", {"intent": "block-1"}, parent_id="model"),
            Entity("block-1-line", "Element@1", {
                "component_id": "block-1", "producer": "wall", "params": {"thickness": 0.3, "height": 3.0},
                "references": {"base": {"level": "ground"},
                               "line": {"from": {"point": [0.0, 0.0]}, "to": {"point": [4.0, 0.0]}}}},
                parent_id="block-1"),
            Entity("block-2", "Component@1", {"intent": "block-2"}, parent_id="model"),
            Entity("block-2-line", "Element@1", {
                "component_id": "block-2", "producer": "wall", "params": {"thickness": 0.3, "height": 3.0},
                "references": {"base": {"level": "ground"},
                               "line": {"from": {"host": {"element": "block-1-line", "along": 4.0}},
                                        "to": {"point": [4.0, 3.0]}}}},
                parent_id="block-2"),
        )
        result = _compile("set_height(get('block-2'), 4)", record)
        view = _view(_apply(record, result))
        self.assertIsNotNone(view["block-2"]["bounds"])
        self.assertEqual(result.report[0]["bounds"], view["block-2"]["bounds"])
        self.assertEqual(result.report[0]["bounds"][1][1], 4.0)


class RecessTests(ConstructionTestCase):
    """#419 round 4: a cutter may read its host's top; the host names it only as an object to consume."""

    RECESS = "\n".join(["mass = extrude(rect(0, 0, 4, 4), 1)",
                        "recess = extrude(rect(1, 1, 2, 2), 0.5, at=top(mass) - 0.2)", "cut(mass, recess)"])
    REBASED = "\n".join(["mass = extrude(rect(0, 0, 4, 4), 1)", "k = extrude(rect(1, 1, 2, 2), 0.5)", "cut(mass, k)",
                         "set_base(k, top(mass) - 0.2)"])
    STACKED = "\n".join(["mass = extrude(rect(0, 0, 4, 4), 1)", "s = extrude(rect(0, 0, 1, 1), 1, at=top(mass))",
                         "c = extrude(rect(2, 2, 0.5, 0.5), 3, at=top(s) - 2.5)", "cut(mass, c)"])

    def volumes(self, record: StateRecord, result) -> dict[str, float] | None:
        """The successor applied and checked, its program compiled as the runner does, then built with OCCT."""

        from archflow.adapters import occt_backend
        from tests.test_occt_execution import _compile as compile_program

        successor = _apply(record, result)
        program = compile_program(successor)
        if not occt_backend.occt_available():  # pragma: no cover - the OCCT build is the evidence when it is installed
            return None
        build = occt_backend.build_program_shapes(program)
        return {object_id: occt_backend.measure_shape(build.objects[object_id].shape).volume
                for object_id in build.physical_object_ids}

    def test_a_cutter_standing_on_its_host_is_produced_and_removed(self) -> None:
        record = _record()
        for script, host, removed in ((self.RECESS, "mass", 2 * 2 * 0.2), (self.REBASED, "mass", 2 * 2 * 0.2),
                                      (self.STACKED, "mass", 0.5 * 0.5 * 1.0)):
            with self.subTest(script=script):
                result = _compile(script, record)
                cutter = result.report[0]["cuts"][0]
                self.assertEqual(_elements(result)[f"{cutter}-body"]["fields"]["references"]["base"]["datum"][-4:], "-top")
                volumes = self.volumes(record, result)
                if volumes is not None:
                    self.assertAlmostEqual(volumes[f"obj-{host}-body"], 16.0 - removed, places=6)
                    self.assertIn(f"obj-{cutter}-body", volumes)  # the cutter stays in the model, hidden
                view = _view(_apply(record, result))
                self.assertEqual(view[cutter]["hidden"], True)

    def test_existing_geometry_standing_on_its_host_can_cut_it(self) -> None:
        record = _record()
        record = _apply(record, _compile("a = extrude(rect(0, 0, 4, 3), 3)\nb = extrude(rect(1, 1, 1, 1), 1, at=top(a) - 0.5)",
                                         record))
        result = _compile("cut(get('a'), get('b'))", record)
        self.assertEqual(result.summary, "construction: cut a")
        volumes = self.volumes(record, result)
        if volumes is not None:
            self.assertAlmostEqual(volumes["obj-a-body"], 36.0 - 0.5, places=6)
            self.assertAlmostEqual(volumes["obj-b-body"], 1.0, places=6)

    def test_a_true_support_cycle_is_still_refused_at_its_line(self) -> None:
        self.refusals([
            ("a = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(0, 0, 1, 1), 1, at=top(a))\nset_base(a, top(b))", 3,
             "a would stand on its own top"),
            ("a = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(0, 0, 1, 1), 1, at=top(a))\ncut(a, b)\n"
             "set_base(a, top(b))", 4, "nothing can stand on a cutter's top"),
        ])


def _block(identifier: str, profile: list, height: float, *, elevation: float = 0.0, voids: tuple = ()) -> tuple:
    """A block of the record as a script leaves one: its component and its ``-body`` element."""

    references: dict = {"base": {"level": "ground"}}
    if voids:
        references["voids"] = list(voids)
    params: dict = {"profile": profile, "height": height}
    if elevation:
        params["elevation"] = elevation
    return (Entity(identifier, "Component@1", {"intent": identifier}, parent_id="model"),
            Entity(identifier + ELEMENT_SUFFIX, "Element@1", {"component_id": identifier, "producer": "prism",
                                                              "references": references, "params": params},
                   parent_id=identifier))


class CutExtentTests(ConstructionTestCase):
    """#419 final round: a cut whose result the bounds that certify an export cannot follow is refused at the line
    of the cut that made it, while the script is still the agent's to fix; the candidate never fails for it later."""

    MASS = "mass = extrude(rect(0, 0, 4, 4), 3)"
    CORNER = "notch = extrude(rect(-1, -1, 2, 2), 5, at=-1)"
    SQUARE = [[0, 0], [4, 0], [4, 4], [0, 4]]

    def test_a_corner_notch_is_refused_at_the_line_of_its_cut(self) -> None:
        for script, line in (
            ("\n".join([self.MASS, self.CORNER, "cut(mass, notch)"]), 3),
            # The cut made the relation; what later reshapes the host does not move the refusal off it.
            ("\n".join([self.MASS, self.CORNER, "cut(mass, notch)", "set_height(mass, 4)"]), 3),
            ("\n".join([self.MASS, "notch = extrude(rect(1, 1, 1, 1), 1)", "cut(mass, notch)",
                        "move(notch, dx=-1.5, dz=-1.5)", "set_height(notch, 5)"]), 3),
        ):
            with self.subTest(script=script):
                error = self.refused(script)
                self.assertEqual((error.line, error.source_line), (line, "cut(mass, notch)"), error.message)
                self.assertIn("notch would cut away a whole corner or side of mass, which a cut cannot do yet",
                              error.message)
                self.assertIn("keep notch within the outer extent of mass", error.message)

    def test_a_notch_that_leaves_every_vertex_is_accepted_with_its_bounds(self) -> None:
        record = _record()
        result = _compile("\n".join([self.MASS, "notch = extrude(rect(1, -1, 2, 2), 5, at=-1)", "cut(mass, notch)"]),
                          record)
        report = {row["id"]: row for row in result.report}
        self.assertEqual(report["mass"]["bounds"], [[0.0, 0.0, 0.0], [4.0, 3.0, 4.0]])
        self.assertEqual(report["mass"]["cuts"], ["notch"])
        self.assertEqual(_view(_apply(record, result))["mass"]["bounds"], report["mass"]["bounds"])

    def test_the_cutter_the_bounds_refuse_is_named_among_the_others(self) -> None:
        # Four cutters in a loop; only the first takes a whole corner, and it is the one named.
        script = "\n".join(["mass = extrude(rect(0, 0, 12, 8), 3)", "for i in range(4):",
                            "    w = extrude(rect(-0.5 + 3 * i, -0.2, 1.2, 0.6), 4, at=-0.5)", "    cut(mass, w)"])
        error = self.refused(script)
        self.assertEqual((error.line, error.source_line), (4, "cut(mass, w)"))
        self.assertIn("w-1 would cut away a whole corner or side of mass", error.message)
        for other in ("w-2", "w-3", "w-4"):
            self.assertNotIn(other, error.message)

    def test_cutters_refused_only_together_are_named_together_at_the_cut_that_joined_them(self) -> None:
        # Each takes one end of the same corner edge: either alone leaves it, both together take it away.
        error = self.refused("\n".join([self.MASS, "a = extrude(rect(-1, -1, 2, 2), 2, at=-1)",
                                        "b = extrude(rect(-1, -1, 2, 2), 2, at=2)", "cut(mass, a)", "cut(mass, b)"]))
        self.assertEqual((error.line, error.source_line), (5, "cut(mass, b)"))
        self.assertIn("a and b together would cut away a whole corner or side of mass", error.message)

    def test_a_cutter_that_misses_its_host_is_refused_at_its_cut(self) -> None:
        error = self.refused("\n".join([self.MASS, "away = extrude(rect(10, 10, 1, 1), 1)", "cut(mass, away)"]))
        self.assertEqual((error.line, error.source_line), (3, "cut(mass, away)"))
        self.assertIn("away does not reach mass, so the cut would remove nothing", error.message)

    def test_existing_geometry_is_refused_where_the_script_changed_it(self) -> None:
        record = _record(*_block("mass", self.SQUARE, 3.0), *_block("c", [[1, 1], [2, 1], [2, 2], [1, 2]], 1.0))
        record = _apply(record, _compile("cut(get('mass'), get('c'))", record))
        cases = (
            # A new cutter cut into existing geometry: the cut's line.
            ("x = 1\n" + self.CORNER + "\ncut(get('mass'), notch)", 3, "notch", "cut(get('mass'), notch)"),
            # An existing cutter moved onto a corner: no cut here made the relation, so the line that moved it.
            ("x = 1\nmove(get('c'), dx=-1.5, dz=-1.5)\nset_height(get('c'), 5)", 3, "c", "set_height(get('c'), 5)"),
        )
        for script, line, cutter, source in cases:
            with self.subTest(script=script):
                error = self.refused(script, record)
                self.assertEqual((error.line, error.source_line), (line, source), error.message)
                self.assertIn(f"{cutter} would cut away a whole corner or side of mass", error.message)

    def test_a_refusal_among_geometry_the_script_never_reached_keeps_its_bounds_unknown(self) -> None:
        record = _record(*_block("mass", self.SQUARE, 3.0, voids=("notch-body",)),
                         *_block("notch", [[-1, -1], [1, -1], [1, 1], [-1, 1]], 5.0, elevation=-1.0))
        self.assertIsNone(_view(record)["mass"]["bounds"])
        result = _compile("block = extrude(rect(1, 1, 1, 1), 1, at=top(get('mass')))", record)
        self.assertEqual(result.report[0]["bounds"], [[1.0, 3.0, 1.0], [2.0, 4.0, 2.0]])
        # Changing the host or its cutter makes the refusal the script's, at the line that changed it.
        for script in ("x = 1\nset_height(get('mass'), 3.5)", "x = 1\nset_height(get('notch'), 6)"):
            with self.subTest(script=script):
                error = self.refused(script, record)
                self.assertEqual(error.line, 2, error.message)
                self.assertIn("notch would cut away a whole corner or side of mass", error.message)


class VocabularyTests(ConstructionTestCase):
    def test_the_vocabulary_passes_the_layer_rule_and_its_example_builds(self) -> None:
        contract = vocabulary()
        self.assertEqual(layer_rule_violations(json.dumps(contract)), ())
        for key in ("conventions", "language", "limits", "verbs", "example"):
            self.assertIn(key, contract)
        identity, errors = contract["conventions"]["identity"], contract["conventions"]["errors"]
        self.assertIn("12 hex digits", identity)
        self.assertIn("name cutters", identity)
        self.assertIn("uncut(host)", identity)
        self.assertIn("after the whole script", errors)
        record = _record()
        result = _compile(contract["example"], record)
        self.assertIn("top(", contract["example"])
        self.assertIn("for ", contract["example"])
        self.assertEqual(sum(1 for row in result.report if row["cuts"]), 1)
        self.assertEqual(len(result.report[0]["cuts"]), 4)
        _operations(_apply(record, result))


class GeometryViewTests(ConstructionTestCase):
    def test_the_view_lists_forms_bounds_and_cuts(self) -> None:
        record = _record()
        self.assertEqual(geometry_view(record), ())
        view = _view(self.cut_record())
        self.assertEqual(view["mass"], {"id": "mass", "form": "solid", "bounds": [[0.0, 0.0, 0.0], [12.0, 3.0, 8.0]],
                                        "cuts": ["w-1", "w-2", "w-3", "w-4"], "cutBy": [], "hidden": False})
        self.assertEqual(view["w-1"], {"id": "w-1", "form": "solid", "bounds": [[1.0, 0.9, -0.2], [2.2, 2.4, 0.4]],
                                       "cuts": [], "cutBy": ["mass"], "hidden": True})
        self.assertNotIn("model", view)

    def test_a_component_with_several_parts_lists_them(self) -> None:
        record = _record(
            Entity("pair", "Component@1", {"intent": "pair"}, parent_id="model"),
            Entity("pair-a", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": 1}}),
            Entity("pair-b", "Element@1", {"component_id": "pair", "producer": "prism",
                                           "references": {"base": {"level": "ground"}},
                                           "params": {"profile": [[2, 0], [3, 0], [3, 1], [2, 1]], "height": 2}}),
        )
        row = _view(record)["pair"]
        self.assertEqual((row["form"], row["parts"], row["bounds"]),
                         ("solid", ["pair-a", "pair-b"], [[0.0, 0.0, 0.0], [3.0, 2.0, 1.0]]))
        error = self.refused("x = 1\nget('pair')", record)
        self.assertEqual(error.line, 2)
        self.assertIn("several parts", error.message)
        result = _compile("move(get('pair-b'), dx=1)", record)
        self.assertEqual(result.report[0]["id"], "pair-b")


if __name__ == "__main__":
    unittest.main()
