"""#419: a construction script lowers to Component@1/Element@1 rows; producers are chosen here only.

The record is what ``initialize_modeling`` seeds (a modelling root ``model`` and a level
``ground`` at zero). Every lowered result is applied the way the semantic-edit path applies
it (existing fields merged), checked by ``validate_element_contract`` and produced.
"""
from __future__ import annotations

import json
import math
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


def _record(*extra: Entity, parameters: tuple[Parameter, ...] = ()) -> StateRecord:
    return StateRecord(
        project_id="demo", run_id="authored",
        entities=(
            Entity("model", "Component@1", {"intent": "Root for candidate modeling", "source_refs": [EVIDENCE]}),
            Entity("ground", "Level@1", {"role": "ground", "elevation": 0.0}, basis_refs=(EVIDENCE,)),
            *extra,
        ),
        parameters=parameters, evidence_refs=(EVIDENCE,), option={"option_id": "modeling"},
        base=ProjectVersionRef("demo", 0, "0" * 64),
    )


def _raised_record() -> StateRecord:
    """The same seed with the ground level at 1 m: numbers and planes are measured from the project's zero."""

    record = _record()
    ground = Entity("ground", "Level@1", {"role": "ground", "elevation": 1.0}, basis_refs=(EVIDENCE,))
    return StateRecord(project_id=record.project_id, run_id=record.run_id,
                       entities=tuple(ground if e.entity_id == "ground" else e for e in record.entities),
                       evidence_refs=record.evidence_refs, option=record.option, base=record.base)


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


LOOP = "\n".join([
    "mass = extrude(rect(0, 0, 12, 8), 3)",
    "for i in range(4):",
    "    w = extrude(rect(1 + 2.8 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)",
    "    cut(mass, w)",
])


class ConstructionTestCase(unittest.TestCase):
    def refused(self, script: str, record: StateRecord | None = None) -> ConstructionError:
        with self.assertRaises(ConstructionError) as caught:
            _compile(script, record)
        self.assertEqual(layer_rule_violations(caught.exception.message), (), caught.exception.message)
        return caught.exception


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
        result = _compile("pit = extrude(rect(0, 0, 2, 2), -1.5)", record)
        self.assertEqual(_elements(result)["pit-body"]["fields"]["params"], {
            "profile": [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]], "height": 1.5,
            "work_plane": {"origin": [0.0, 0.0, 0.0], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 0.0, 1.0],
                           "normal": [0.0, -1.0, 0.0]}})
        self.assertEqual(result.report[0]["bounds"], [[0.0, -1.5, 0.0], [2.0, 0.0, 2.0]])
        self.assertEqual(_view(_apply(record, result))["pit"]["bounds"], [[0.0, -1.5, 0.0], [2.0, 0.0, 2.0]])

    def test_a_profile_drawn_on_a_plane_extrudes_along_its_normal(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "panel = extrude(rect(0, 0, 2, 3), 0.5, plane=front(z=1))",
            "flank = extrude(rect(0, 0, 2, 3), 0.5, plane=side(x=1))",
            "tilted = extrude(rect(0, 0, 1, 1), 0.2, plane=plane((5, 1, 0), (1, 0, 0), (0, 1, 1)))",
        ]), record)
        panel = _elements(result)["panel-body"]["fields"]
        self.assertEqual(panel["references"], {"base": {"level": "ground"}})
        self.assertEqual(panel["params"], {
            "profile": [[0.0, 0.0], [2.0, 0.0], [2.0, 3.0], [0.0, 3.0]], "height": 0.5,
            "work_plane": {"origin": [0.0, 0.0, 1.0], "xAxis": [1.0, 0.0, 0.0], "yAxis": [0.0, 1.0, 0.0],
                           "normal": [0.0, 0.0, 1.0]}})
        operations = _operations(_apply(record, result))

        def corners(op_id: str) -> set:
            params = _params(operations[op_id])
            base = [tuple(point) for point in params["profile"]]
            far = [tuple(c + v for c, v in zip(point, params["vector"])) for point in base]
            return {tuple(round(c, 6) for c in point) for point in base + far}

        self.assertEqual(corners("panel-body"), {(x, y, z) for x in (0.0, 2.0) for y in (0.0, 3.0) for z in (1.0, 1.5)})
        self.assertEqual(corners("flank-body"), {(x, y, z) for x in (1.0, 1.5) for y in (0.0, 3.0) for z in (0.0, 2.0)})
        s = math.sqrt(0.5)
        tilted = {(5.0 + u, round(1.0 + v * s - n * s, 6), round(v * s + n * s, 6))
                  for u in (0.0, 1.0) for v in (0.0, 1.0) for n in (0.0, 0.2)}
        self.assertEqual(corners("tilted-body"), {tuple(round(c, 6) for c in point) for point in tilted})
        report = {row["id"]: row for row in result.report}
        self.assertEqual(report["flank"]["bounds"], [[1.0, 0.0, 0.0], [1.5, 3.0, 2.0]])

    def test_numbers_and_planes_are_measured_from_the_project_zero(self) -> None:
        record = _raised_record()
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


class OtherFormsLoweringTests(ConstructionTestCase):
    def test_face_path_and_loft_lower_to_rows_that_produce(self) -> None:
        record = _record()
        result = _compile("\n".join([
            "plate = face(rect(0, 0, 4, 3), at=0.5)",
            "edge = path([(0, 1, 0), (4, 1, 0), (4, 1, 3)])",
            "ramp = path([(0, 0, 0), (4, 1, 0)])",
            "spire = loft([section(rect(0, 0, 2, 2), 0), section(rect(0.5, 0.5, 1, 1), 3)])",
            "sheet = loft([section(rect(0, 5, 2, 2), 0), section(rect(0, 5, 2, 2), 1)], cap=False)",
        ]), record)
        elements = _elements(result)
        self.assertEqual(elements["plate-body"]["fields"], {
            "component_id": "plate", "producer": "planar-surface", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0], [0.0, 0.0]], "elevation": 0.5}})
        self.assertEqual(elements["edge-body"]["fields"], {
            "component_id": "edge", "producer": "curve", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0]], "elevation": 1.0}})
        self.assertEqual(elements["ramp-body"]["fields"]["producer"], "curve")
        self.assertIn("work_plane", elements["ramp-body"]["fields"]["params"])
        self.assertEqual(elements["spire-body"]["fields"], {
            "component_id": "spire", "producer": "loft", "references": {"base": {"level": "ground"}},
            "params": {"profiles": [[[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 2.0], [0.0, 0.0, 2.0]],
                                    [[0.5, 3.0, 0.5], [1.5, 3.0, 0.5], [1.5, 3.0, 1.5], [0.5, 3.0, 1.5]]],
                       "profile_size": 4, "cap_ends": True}})
        self.assertIs(elements["sheet-body"]["fields"]["params"]["cap_ends"], False)
        view = _view(_apply(record, result))
        self.assertEqual({key: row["form"] for key, row in view.items()},
                         {"plate": "face", "edge": "path", "ramp": "path", "spire": "solid", "sheet": "other"})
        self.assertEqual(view["plate"]["bounds"], [[0.0, 0.5, 0.0], [4.0, 0.5, 3.0]])
        self.assertEqual(view["ramp"]["bounds"], [[0.0, 0.0, 0.0], [4.0, 1.0, 0.0]])
        self.assertEqual(view["spire"]["bounds"], [[0.0, 0.0, 0.0], [2.0, 3.0, 2.0]])

    def test_a_path_that_leaves_its_plane_is_refused(self) -> None:
        error = self.refused("x = 1\np = path([(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)])")
        self.assertEqual(error.line, 2)
        self.assertIn("one plane", error.message)

    def test_profiles_are_checked_where_they_are_made(self) -> None:
        cases = [
            ("p = polygon([(0, 0), (1, 0), (1, 1), (0, 0)])", "a closed profile does not repeat its first point"),
            ("p = polygon([(0, 0), (1, 0), (1, 0), (0, 1)])", "repeat"),
            ("p = polygon([(0, 0), (1, 0), (2, 0)])", "area"),
            ("p = polygon([(0, 0), (2, 2), (2, 0), (0, 1)])", "crosses itself"),
            ("p = circle(0, 0, 1, segments=4)", "8"),
            ("p = offset(rect(0, 0, 1, 1), -0.5)", "offset(-0.5) collapses this profile"),
        ]
        for script, words in cases:
            with self.subTest(script=script):
                error = self.refused("x = 1\n" + script)
                self.assertEqual(error.line, 2)
                self.assertIn(words, error.message)

    def test_circle_and_offset_make_profiles(self) -> None:
        result = _compile("\n".join([
            "disc = extrude(circle(0, 0, 1, segments=8), 1)",
            "rim = extrude(offset(rect(0, 0, 2, 2), 0.5), 1)",
            "print(bounds(rim))",
        ]))
        profile = _elements(result)["disc-body"]["fields"]["params"]["profile"]
        self.assertEqual(profile[0], [1.0, 0.0])
        self.assertEqual(profile[2], [0.0, 1.0])  # counter-clockwise: +x turns toward +z
        self.assertEqual(result.log, ("((-0.5, 0.0, -0.5), (2.5, 1.0, 2.5))",))


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
            "post = extrude(rect(0, 0, 0.5, 0.5), 3)",
            "cut(post, extrude(rect(0.1, 0.1, 0.3, 0.3), 1, at=1))",
            "row = array(post, 3, dx=2)",
            "twin = copy(post, dz=4)",
        ]), record)
        elements = _elements(result)
        self.assertEqual(sorted(elements), ["post-body", "post-cut-1-body", "row-1-body", "row-2-body", "twin-body"])
        self.assertEqual(elements["post-body"]["fields"]["references"]["voids"], ["post-cut-1-body"])
        self.assertEqual(elements["row-2-body"]["fields"]["params"]["profile"][0], [4.0, 0.0])
        self.assertEqual(elements["row-1-body"]["fields"]["references"], {"base": {"level": "ground"}})
        self.assertEqual(elements["twin-body"]["fields"]["params"]["profile"][0], [0.0, 4.0])
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
        self.assertEqual(host["references"]["voids"], ["w-1-body", "w-2-body", "w-3-body", "w-4-body"])
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

    def test_ids_come_from_names_then_variables_then_cuts_then_order(self) -> None:
        result = _compile("\n".join([
            "base = extrude(rect(0, 0, 10, 10), 2)",
            "Upper_Part = extrude(rect(1, 1, 2, 2), 1, at=top(base))",
            "name(extrude(rect(5, 5, 1, 1), 1), 'spot')",
            "extrude(rect(8, 8, 1, 1), 1)",
            "cut(base, extrude(rect(3, 3, 1, 1), 1))",
            "extrude(rect(8, 0, 1, 1), 1)",
            "stack = [extrude(rect(i, 12, 0.5, 0.5), 1) for i in range(2)]",
        ]))
        self.assertEqual([row["id"] for row in result.report],
                         ["base", "upper-part", "spot", "shape-1", "base-cut-1", "shape-2", "stack-1", "stack-2"])

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

    def test_two_shapes_with_one_id_are_refused_at_the_second(self) -> None:
        error = self.refused("a = extrude(rect(0, 0, 1, 1), 1)\nb = extrude(rect(2, 0, 1, 1), 1)\nname(b, 'a')")
        self.assertEqual(error.line, 3)

    def test_an_id_a_variable_cannot_spell_asks_for_a_name(self) -> None:
        error = self.refused("x = 1\n_tmp = extrude(rect(0, 0, 1, 1), 1)")
        self.assertEqual(error.line, 2)
        self.assertIn("name(", error.message)
        self.assertEqual(self.refused("a = name(extrude(rect(0, 0, 1, 1), 1), 'mass-body')").line, 1)


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
        result = _compile("m = get('mass')\nhole = extrude(rect(1, -0.1, 1, 0.5), 1, at=1)\ncut(m, hole)", record)
        self.assertEqual(_elements(result)["mass-body"]["fields"]["references"],
                         {"base": {"level": "ground"}, "voids": ["hole-body"]})
        successor = _apply(record, result)
        self.assertEqual(_view(successor)["hole"]["cutBy"], ["mass"])
        uncut = _compile("uncut(get('mass'), get('hole'))", successor)
        self.assertEqual(uncut.entities[0]["fields"]["references"], {"base": {"level": "ground"}})
        self.assertEqual(uncut.summary, "construction: uncut mass")

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

    def test_a_copy_and_a_new_base_of_existing_geometry_are_lowered_and_produce(self) -> None:
        record = self._massed()
        result = _compile("\n".join([
            "twin = copy(get('mass'), dx=10)",
            "base = extrude(rect(20, 0, 5, 5), 2)",
            "set_base(get('mass'), top(base))",
            "print(bounds(get('mass')))",
        ]), record)
        elements = _elements(result)
        self.assertEqual(elements["twin-body"]["fields"], {
            "component_id": "twin", "producer": "prism", "references": {"base": {"level": "ground"}},
            "params": {"profile": [[10.0, 0.0], [14.0, 0.0], [14.0, 3.0], [10.0, 3.0]], "height": 3.0}})
        self.assertEqual(elements["mass-body"]["fields"]["references"], {"base": {"datum": "base-body-top"}})
        self.assertEqual(result.log, ("((0.0, 2.0, 0.0), (4.0, 5.0, 3.0))",))
        self.assertEqual(_view(_apply(record, result))["mass"]["bounds"], [[0.0, 2.0, 0.0], [4.0, 5.0, 3.0]])

    def test_edits_of_existing_geometry_refuse_at_their_line(self) -> None:
        record = self._massed()
        cut = _apply(record, _compile("hole = extrude(rect(1, -0.1, 1, 0.5), 1, at=1)\ncut(get('mass'), hole)", record))
        error = self.refused("x = 1\ndelete(get('hole'))", cut)
        self.assertEqual(error.line, 2)
        self.assertIn("still cuts mass", error.message)
        bound = _record(Entity("bar", "Component@1", {"intent": "bar"}, parent_id="model"),
                        Entity("bar-body", "Element@1", {"component_id": "bar", "producer": "prism",
                                                         "references": {"base": {"level": "ground"}},
                                                         "params": {"profile": [[0, 0], ["@h", 0], ["@h", 1], [0, 1]],
                                                                    "height": 1}}, parent_id="bar"),
                        parameters=PARAMETERS)
        error = self.refused("b = get('bar')\nmove(b, dx=1)", bound)
        self.assertEqual(error.line, 2)
        self.assertIn("bound to project parameters", error.message)
        self.assertEqual(_compile("print(bounds(get('bar')))", bound).log, ("((0.0, 0.0, 0.0), (3.0, 1.0, 1.0))",))

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
        cases = [
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
        ]
        for script, line, words in cases:
            with self.subTest(script=script):
                error = self.refused(script)
                self.assertEqual(error.line, line)
                self.assertIn(words, error.message)


class AnchorRefusalTests(ConstructionTestCase):
    def test_anchor_refusals_name_their_line(self) -> None:
        cases = [
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
        ]
        for script, line, words in cases:
            with self.subTest(script=script):
                error = self.refused(script, _record(parameters=PARAMETERS))
                self.assertEqual(error.line, line)
                self.assertIn(words, error.message)


class VocabularyTests(ConstructionTestCase):
    def test_the_vocabulary_passes_the_layer_rule_and_its_example_builds(self) -> None:
        contract = vocabulary()
        self.assertEqual(layer_rule_violations(json.dumps(contract)), ())
        for key in ("conventions", "language", "limits", "verbs", "example"):
            self.assertIn(key, contract)
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
        view = _view(_apply(record, _compile(LOOP, record)))
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
