"""Small actions become exact-record edits without model-authored metadata."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

import project_runtime  # noqa: F401
from jsonschema import Draft202012Validator
from archflow.project.refs import ProjectVersionRef
from archflow.state.state_record import Entity, Lineage, Parameter, StateRecord, apply_state_record_operator
from project_runtime.application.intent import component_edit_proposal, parse_utterance
from project_runtime.application.intent_agent import _parse_answer, _strict_response_schema, record_sheet, response_schema, Selection
from project_runtime.application.intent_context import IntentContext, compile_context, model_context
from project_runtime.application.intent_requests import action_answer, action_preflight, provider_schema, request_schema, validate_request_answer
from project_runtime.application.projection import _elements
from project_runtime.errors import StudioError


def _record(*, bound=False, producer="wall", parameter_unit="m"):
    fields = {
        "component_id": "facade", "producer": producer,
        "references": {"base": {"level": "level-02"}, "line": {
            "from": {"axis_point": {"axis": "front", "along": 0}},
            "to": {"axis_point": {"axis": "front", "along": 4}}, "inward": [1, 0],
        }},
        "params": {"height": "@wall_height" if bound else 3.0, "thickness": 0.2, "openings": []},
        "type_ref": "wall-type", "name": "Courtyard wall", "host": "support-wall",
        "custom_metadata": {"finish": "brick", "pending_note": None},
    }
    return StateRecord(
        "actions-project", "actions-run",
        entities=(
            Entity("building", "Component@1", {"semantic_kind": "building"}),
            Entity("facade", "Component@1", {"semantic_kind": "controlled-entry"}, "building"),
            Entity("level-02", "Level@1", {"role": "upper", "elevation": 3.0}, basis_refs=("reading:measured-wall",)),
            Entity("axis-front", "GridAxis@1", {"role": "front", "origin": [0, 0, 0], "direction": [1, 0, 0]}, basis_refs=("reading:measured-wall",)),
            Entity("wall-type", "Type@1", {"producer": producer, "params": {"thickness": 0.15}}),
            Entity("support-wall", "Element@1", {"producer": "prism", "component_id": "facade",
                   "references": {"base": {"level": "level-02"}},
                   "params": {"height": 3.0, "profile": [[0, 0], [4, 0], [4, 0.2], [0, 0.2]]}},
                   "facade", ("reading:measured-wall",)),
            Entity("wall-07", "Element@1", fields, "facade", ("reading:measured-wall",), Lineage(introduced_at="design-02")),
        ),
        parameters=(Parameter("wall_height", 3.0, parameter_unit, epistemic_status="hypothesis",
                              source_ref="reading:measured-wall", lineage=Lineage(introduced_at="design-01")),),
        base=ProjectVersionRef("actions-project", 0, "0" * 64),
        decision_ref="decision:authored-dimensions",
        basis_refs=("studio:intent",), evidence_refs=("reading:measured-wall",),
    )


def _projection(record):
    elements, error = _elements(record)
    if error:
        raise AssertionError(error)
    return SimpleNamespace(project_id=record.project_id, record=record, components=None,
                           elements=elements, parameters=record.parameters, honesty=(),
                           state_digest=record.state_digest, record_digest=record.digest,
                           edges=record.dependency_edges())


def _context(record, *, tier="scalar", fields=("height",)):
    sheet = record_sheet(_projection(record), Selection("facade", "wall-07"))
    return IntentContext(tier, sheet, target_ids=("wall-07",), producer_ids=(record.entity("wall-07").fields["producer"],), editable_fields=fields)


def _action(field="height", value=4.0, unit=None, op="set_parameter"):
    return {"op": op, "field": field, "value": value, "unit": unit}


def _answer(*actions, status="compiled", question=None, refs=()):
    return {"status": status, "actions": list(actions), "why": "Apply the requested dimensions.",
            "question": question, "contextRefs": list(refs)}


def _design_answer(**changes):
    """A design answer in the construction contract: one script, nothing classified."""

    answer = {
        "status": "compiled", "script": "block = extrude(rect(0, 0, 2, 2), 3, at=level('level-02'))",
        "facets": None, "parameters": None, "keep": None, "utterance": None, "targetId": None,
        "why": "Add a block beside the selected geometry.", "question": None, "contextRefs": [],
    }
    answer.update(changes)
    return answer


def _strict_facets(**values):
    """One facet target as a strict provider writes it: every registered key present, unset ones null."""

    from archflow.semantics.facets import FACETS

    return {"id": "facade", "set": {key: values.get(key) for key in FACETS}, "remove": None}


class IntentRequestSchemaTests(unittest.TestCase):
    def schema(self, context):
        return request_schema(context, response_schema(strict=False))

    def assertRejected(self, answer, context):
        with self.assertRaises(ValueError):
            validate_request_answer(answer, context, self.schema(context))

    def test_local_design_answers_in_the_construction_contract_only(self):
        record = _record()
        sheet = record_sheet(_projection(record), Selection("facade", "wall-07"))
        context = compile_context("replace wall-07 while keeping its support", sheet, record=record)
        self.assertIsNotNone(context.design_sheet)
        schema = self.schema(context)
        # A script is typed here and its writes are checked against the targets once it is compiled.
        validate_request_answer(_design_answer(), context, schema)
        validate_request_answer(_design_answer(script=None, facets=[{"id": "facade", "set": {"architectural.role": "wall"}}]),
                                context, schema)
        for wrong in (
            # A scalar sentence is not a local design answer, and it excludes every other change.
            _design_answer(script=None, utterance="set parameter:wall_height to 4"),
            _design_answer(utterance="set height to 4"),
            # Compiled says what changes.
            _design_answer(script=None),
            # A question or a limitation changes nothing.
            _design_answer(status="question", question="Which side?"),
            _design_answer(status="unsupported"),
            # The old component edit and its two ids are no part of the answer.
            {**_design_answer(), "semanticEdit": None},
            {**_design_answer(), "targetComponentId": "facade"},
            {**_design_answer(), "elementId": "wall-07"},
            # Facets are text values of registered keys, and only registered values of a closed key.
            _design_answer(script=None, facets=[{"id": "facade", "set": {"architectural.kind": "wall"}}]),
            _design_answer(script=None, facets=[{"id": "facade", "set": {"architectural.role": "tower"}}]),
        ):
            with self.subTest(answer=wrong):
                self.assertRejected(wrong, context)

    def test_local_design_cannot_write_locked_shared_or_unrelated_parameters(self):
        for blocker in ("locked", "shared", "unrelated"):
            with self.subTest(blocker=blocker):
                record = _record(bound=True)
                if blocker == "locked":
                    record = replace(record, parameters=(replace(record.parameters[0], lock_authority="review:locked"),))
                elif blocker == "shared":
                    other = record.entity("support-wall")
                    other = replace(other, fields={**other.fields, "params": {**other.fields["params"], "height": "@wall_height"}})
                    record = replace(record, entities=tuple(other if row.entity_id == other.entity_id else row for row in record.entities))
                else:
                    record = replace(record, parameters=(*record.parameters, Parameter("unrelated", 2, "m")))
                sheet = record_sheet(_projection(record), Selection("facade", "wall-07"))
                context = compile_context("reconfigure wall-07 while keeping its base", sheet, record=record)
                answer = _design_answer(script=None, parameters=[{
                    "key": "unrelated" if blocker == "unrelated" else "wall_height", "value": 4,
                    "unit": "m", "inputs": [], "epistemic_status": "declared",
                }])
                self.assertRejected(answer, context)
        # The target's own control, neither locked, derived nor shared, may change.
        record = _record(bound=True)
        sheet = record_sheet(_projection(record), Selection("facade", "wall-07"))
        context = compile_context("reconfigure wall-07 while keeping its base", sheet, record=record)
        validate_request_answer(_design_answer(script=None, parameters=[{"key": "wall_height", "value": 4}]),
                                context, self.schema(context))

    def test_narrow_schema_accepts_actions_without_ids_or_upsert_payloads(self):
        record = _record()
        for tier, fields in (("scalar", ("height",)), ("component", ("height", "thickness"))):
            context = _context(record, tier=tier, fields=fields)
            answer = _answer(*(_action(field) for field in fields))
            validate_request_answer(answer, context, self.schema(context))
            for key, value in (("targetId", "wall-08"), ("script", "block = extrude(rect(0, 0, 1, 1), 1)"),
                               ("semanticEdit", {}), ("utterance", "set height to 4")):
                with self.subTest(tier=tier, field=key):
                    self.assertRejected({**answer, key: value}, context)
            for key, value in (("target", "wall-08"), ("basis_refs", []), ("expr", "2 * other")):
                bad = deepcopy(answer)
                bad["actions"][0][key] = value
                self.assertRejected(bad, context)

    def test_component_requires_exact_fields_once_with_no_partial_scalar_answer(self):
        context = _context(_record(), tier="component", fields=("height", "thickness"))
        for actions in ((), (_action(),), (_action(), _action()), (_action(), _action("width")),
                        (_action(), _action("thickness"), _action("thickness"))):
            with self.subTest(actions=actions):
                self.assertRejected(_answer(*actions), context)
        self.assertRejected(_design_answer(), context)

    def test_nonfinite_values_unknown_ops_units_and_fields_are_rejected(self):
        context = _context(_record())
        for value in (True, float("nan"), float("inf"), -float("inf"), 10 ** 400):
            with self.subTest(value=repr(value)[:30]):
                self.assertRejected(_answer(_action(value=value)), context)
        for update in ({"op": "delete"}, {"unit": "yards"}, {"field": "type_ref"},
                       {"op": "increase_percent", "unit": "m"}, {"op": "increase_percent", "value": -10}):
            self.assertRejected(_answer({**_action(), **update}), context)

    def test_context_and_question_envelopes_never_contain_actions(self):
        context = _context(_record())
        for answer in (_answer(status="question", question="Which height should it have?"),
                       _answer(status="unsupported"), _answer(status="needs_context", refs=("entity:level-03",))):
            validate_request_answer(answer, context, self.schema(context))
        for answer in (_answer(_action(), status="question", question="Which height?"),
                       _answer(status="question"), _answer(status="needs_context"),
                       _answer(status="needs_context", refs=["entity:level-03"] * 17),
                       _answer(_action(), refs=("entity:level-03",)),
                       _answer(status="unsupported", question="Edit anyway?")):
            self.assertRejected(answer, context)

    def test_server_converts_units_and_uses_actual_record_for_percentages(self):
        record = _record()
        context = _context(record)
        for value, unit, expected in ((1200, "mm", 1.2), (120, "cm", 1.2), (2, "m", 2), (10, "in", 0.254), (2, "ft", 0.6096)):
            with self.subTest(unit=unit):
                result = action_answer(_answer(_action(value=value, unit=unit)), context, record)
                self.assertAlmostEqual(parse_utterance(result["utterance"]).number, expected)
        target = next(row for row in context.sheet["elements"] if row["elementId"] == "wall-07")
        target["numericFields"]["height"] = 100
        for op, expected in (("increase_percent", 3.3), ("decrease_percent", 2.7)):
            result = action_answer(_answer(_action(value=10, unit="%", op=op)), context, record)
            self.assertAlmostEqual(parse_utterance(result["utterance"]).number, expected)

    def test_unknown_unit_allows_bare_numbers_but_asks_for_explicit_units(self):
        # A producer that declares no unit for this field (#404 F17 declares the advertised ones).
        record = _record(producer="retained-legacy")
        context = _context(record)
        self.assertIsNone(action_preflight(context, record))
        result = action_answer(_answer(_action(value=1.25)), context, record)
        self.assertEqual(parse_utterance(result["utterance"]).number, 1.25)
        result = action_answer(_answer(_action(value=1200, unit="mm")), context, record)
        self.assertEqual(result["status"], "question")
        self.assertIsNone(result["utterance"])

    def test_a_producer_declared_unit_converts_an_explicit_length(self):
        # #404 F17: the prism producer declares its height in metres, so 1200 mm is 1.2.
        record = _record(producer="prism")
        context = _context(record)
        result = action_answer(_answer(_action(value=1200, unit="mm")), context, record)
        self.assertEqual(result["status"], "compiled")
        self.assertEqual(parse_utterance(result["utterance"]).number, 1.2)
        # Exact, as the scalar seam is: float factors made 700 mm 0.7000000000000001.
        result = action_answer(_answer(_action(value=700, unit="mm")), context, record)
        self.assertEqual(parse_utterance(result["utterance"]).number, 0.7)

    def test_lengths_convert_exactly_into_the_declared_unit(self):
        # #404 F17: one table serves the scalar seam and this path, with no float drift.
        from project_runtime.application.intent_requests import in_unit
        for value, unit, declared, expected in (
                (1100, "mm", "m", 1.1), (2200, "mm", "m", 2.2), (5, "mm", "m", 0.005), (12.5, "cm", "m", 0.125),
                (700, "mm", "m", 0.7), (10, "ft", "m", 3.048), (12, "in", "m", 0.3048), (1.2, "m", "mm", 1200),
                (3, "Metres", "m", 3), (90, "deg", "deg", 90)):
            with self.subTest(value=value, unit=unit, declared=declared):
                self.assertEqual(in_unit(value, unit, declared), expected)
        self.assertIsNone(in_unit(90, "deg", "m"), "not a length: nothing to convert")
        self.assertIsNone(in_unit(3, "mm", "deg"))

    def test_the_declared_unit_is_read_however_it_is_spelled(self):
        # #404 review of 13f8b7b7: a record may declare "Metres" or "M". The declared
        # side is read through the same aliases as the unit that was said.
        from decimal import Decimal
        from project_runtime.application.intent_requests import in_unit, in_unit_exact
        for value, unit, declared, expected in (
                (2200, "mm", "Metres", 2.2), (2200, "mm", "M", 2.2), (2.2, "m", "millimetres", 2200),
                (35, "cm", " Meter ", 0.35)):
            with self.subTest(declared=declared):
                self.assertEqual(in_unit(value, unit, declared), expected)
                self.assertEqual(in_unit_exact(str(value), unit, declared), Decimal(str(expected)))
        said = 2.25
        self.assertIs(in_unit(said, "metres", "M"), said, "the same unit, however spelled, passes through unchanged")
        for declared, value, unit, expected in (("Millimetres", 1.2, "m", 1200), ("M", 2200, "mm", 2.2)):
            with self.subTest(parameter_unit=declared):
                record = _record(bound=True, parameter_unit=declared)
                result = action_answer(_answer(_action(value=value, unit=unit)), _context(record), record)
                self.assertEqual(parse_utterance(result["utterance"]).number, expected)

    def test_bound_scalar_uses_declared_parameter_units_without_unbinding(self):
        record = _record(bound=True, parameter_unit="mm")
        context = _context(record)
        before = record.to_dict()
        result = action_answer(_answer(_action(value=1.2, unit="m")), context, record)
        parsed = parse_utterance(result["utterance"])
        self.assertEqual(parsed.field, "parameter:wall_height")
        self.assertEqual(parsed.number, 1200)
        self.assertEqual(record.to_dict(), before)

    def test_component_patch_preserves_metadata_through_real_typed_upsert(self):
        record = _record(bound=True)
        context = _context(record, tier="component", fields=("height", "thickness"))
        before = record.to_dict()
        result = action_answer(_answer(_action(value=4000, unit="mm"), _action("thickness", 30, "cm")), context, record)
        # The component tier's own edit travels beside the answer, never inside the provider contract.
        edit = result.pop("semanticEdit")
        compilation = _parse_answer(json.dumps(result), provider="test", model="test", latency_ms=0,
                                    prompt_sha="a" * 64, normalize_fields=False, derived_edit=edit)
        self.assertEqual((compilation.status, compilation.target_id), ("compiled", "wall-07"))
        self.assertTrue(compilation.proposes_change)
        self.assertIsNone(compilation.script)
        # Without the tier's own edit, the same envelope compiles nothing and is refused.
        with self.assertRaises(StudioError):
            _parse_answer(json.dumps(result), provider="test", model="test", latency_ms=0,
                          prompt_sha="a" * 64, normalize_fields=False)
        proposal = component_edit_proposal(_projection(record), compilation.semantic_edit,
                                          utterance="Raise and thicken the wall.", component_id="facade")
        successor = apply_state_record_operator(record, proposal["state_record_operator"])
        self.assertEqual(record.to_dict(), before)
        original_wall = record.entity("wall-07")
        expected_wall = replace(original_wall, fields={**original_wall.fields,
                                "params": {**original_wall.fields["params"], "thickness": 0.3}})
        self.assertEqual(successor.entity("wall-07"), expected_wall)
        self.assertEqual(successor.parameter("wall_height"), replace(record.parameter("wall_height"), value=4.0))
        for entity in record.entities:
            if entity.entity_id != "wall-07":
                self.assertEqual(successor.entity(entity.entity_id), entity)
        self.assertEqual(successor.entity("wall-07").fields["params"]["height"], "@wall_height")
        self.assertIsNone(successor.entity("wall-07").fields["custom_metadata"]["pending_note"])

    def test_locked_and_derived_controls_short_circuit_as_design_answers(self):
        record = _record(bound=True)
        for parameter, expected in ((replace(record.parameter("wall_height"), lock_authority="architect"), "unsupported"),
                                    (replace(record.parameter("wall_height"), expr="3 * module", inputs=("module",)), "question")):
            changed = replace(record, parameters=(parameter, Parameter("module", 1.0, "m")))
            context = _context(changed)
            with self.subTest(status=expected):
                self.assertEqual(action_preflight(context, changed)["status"], expected)
                result = action_answer(_answer(_action()), context, changed)
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["utterance"])
                self.assertNotIn("semanticEdit", result)
                self.assertEqual(result["targetId"], "wall-07")
        wall = record.entity("wall-07")
        wall = replace(wall, fields={**wall.fields, "references": {**wall.fields["references"], "top": {"level": "level-02"}}})
        changed = replace(record, entities=tuple(wall if item.entity_id == wall.entity_id else item for item in record.entities))
        self.assertEqual(action_preflight(_context(changed), changed)["status"], "question")

    def test_shared_parameter_rejects_hidden_nested_and_derived_consumers(self):
        record = _record(bound=True)
        cases = (
            Entity("other", "Element@1", {"component_id": "facade", "producer": "wall", "params": {"height": "@wall_height"}}),
            Entity("other", "Element@1", {"component_id": "facade", "producer": "wall", "references": {"top": {"offset_from": {"level": "level-02", "offset": "@wall_height"}}}}),
            Entity("other", "Type@1", {"producer": "wall", "params": {"height": "@wall_height"}}),
            Entity("other", "Element@1", {"component_id": "facade", "producer": "wall", "params": {"height": "@derived_height"}}),
        )
        for other in cases:
            changed = replace(record, entities=(*record.entities, other),
                              parameters=(*record.parameters, Parameter("derived_height", 6.0, "m", expr="2 * wall_height")))
            context = _context(changed)
            context.sheet["elements"] = [row for row in context.sheet["elements"] if row["elementId"] == "wall-07"]
            with self.subTest(consumer=other.fields):
                result = action_answer(_answer(_action()), context, changed)
                self.assertEqual(result["status"], "question")
                self.assertNotIn("wall_height", result["question"])
                self.assertNotIn("semanticEdit", result)

    def test_same_object_coupling_rejects_unrequested_field_and_conflicting_values(self):
        record = _record(bound=True)
        wall = record.entity("wall-07")
        wall = replace(wall, fields={**wall.fields, "params": {**wall.fields["params"], "thickness": "@wall_height"}})
        record = replace(record, entities=tuple(wall if item.entity_id == wall.entity_id else item for item in record.entities))
        sheet = record_sheet(_projection(record), Selection("facade", "wall-07"))
        single = compile_context("set wall-07 height to 4", sheet, record=record)
        self.assertEqual(single.tier, "scalar")
        self.assertEqual(action_preflight(single, record)["status"], "question")
        self.assertFalse(model_context(single)["targets"][0]["controls"][0]["editable"])
        context = compile_context("set wall-07 height to 4 and thickness to 4", sheet, record=record)
        self.assertEqual(context.tier, "component")
        self.assertEqual(context.editable_fields, ("height", "thickness"))
        self.assertIsNone(action_preflight(context, record))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            action_answer(_answer(_action(value=4), _action("thickness", 5)), context, record)
        result = action_answer(_answer(_action(value=4), _action("thickness", 4)), context, record)
        self.assertEqual(result["semanticEdit"]["parameters"], [{"key": "wall_height", "value": 4}])
        self.assertEqual(result["semanticEdit"]["entities"], [])
        controls = model_context(context)["targets"][0]["controls"]
        self.assertEqual({control["field"]: control["editable"] for control in controls},
                         {"height": True, "thickness": True})

    def test_design_contract_and_factored_strict_validation_are_preserved(self):
        record = _record()
        for tier, fields in (("scalar", ("height",)), ("component", ("height", "thickness")), ("design", ())):
            context = _context(record, tier=tier, fields=fields)
            schema = self.schema(context)
            before = deepcopy(schema)
            strict = _strict_response_schema(schema)
            factored = provider_schema(schema, _strict_response_schema)
            Draft202012Validator.check_schema(factored)
            self.assertEqual(schema, before)
            valid = _design_answer() if tier == "design" else _answer(*(_action(field) for field in fields))
            samples = [valid, {**valid, "unknown": "extra"}, {**valid, "status": "approve"}]
            if tier == "design":
                # Every key a strict provider writes; then an unregistered facet value, and a parameter value as text.
                samples[0] = _design_answer(script=None, facets=[_strict_facets(**{"architectural.role": "wall"})],
                                            parameters=[{"key": "module", "value": 1.5, "unit": "m", "expr": None,
                                                         "inputs": None, "epistemic_status": None, "source_ref": None}])
                samples.append(_design_answer(facets=[_strict_facets(**{"architectural.role": "tower"})]))
                samples.append(_design_answer(parameters=[{"key": "module", "value": "1.5", "unit": "m", "expr": None,
                                                           "inputs": None, "epistemic_status": None, "source_ref": None}]))
                # Factoring never grows a schema; the construction contract has no repeated variants left to share.
                self.assertLessEqual(len(json.dumps(factored)), len(json.dumps(strict)))
            else:
                invalid = deepcopy(valid)
                invalid["actions"][0]["field"] = "removeEntityIds"
                samples.append(invalid)
            for index, sample in enumerate(samples):
                with self.subTest(tier=tier, sample=index):
                    self.assertEqual(Draft202012Validator(strict).is_valid(sample), Draft202012Validator(factored).is_valid(sample))
                    self.assertEqual(Draft202012Validator(factored).is_valid(sample), index == 0)


if __name__ == "__main__":
    unittest.main()
