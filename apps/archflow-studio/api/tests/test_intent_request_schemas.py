"""A smaller output vocabulary must preserve validation and writable scope."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

import archflow_studio_api  # noqa: F401
from jsonschema import Draft202012Validator

from archflow_studio_api.application.intent_agent import _strict_response_schema, response_schema
from archflow_studio_api.application.intent_context import IntentContext
from archflow_studio_api.application.intent_requests import (
    provider_schema,
    request_schema,
    validate_request_answer,
)


def _wall_fields():
    return {
        "component_id": "facade", "producer": "wall", "type_ref": "wall-type",
        "references": {
            "base": {"level": "level-02"},
            "line": {"from": {"grid": "A"}, "to": {"grid": "B"}, "inward": [1, 0]},
        },
        "params": {"height": "@wall-height", "thickness": 0.2, "openings": []},
    }


def _context(tier="component"):
    fields = _wall_fields()
    target = {
        "elementId": "wall-07", "componentId": "facade", "parentId": "facade",
        "producer": "wall", "typeRef": "wall-type", "basisRefs": ["studio:intent"],
        "numericFields": {"height": 3.0, "thickness": 0.2},
        "parameterBindings": {"height": "wall-height"},
        "params": fields["params"], "references": fields["references"],
    }
    dependency = {**deepcopy(target), "elementId": "wall-08"}
    return IntentContext(
        tier, {"elements": [target, dependency], "parameters": [{
            "key": "wall-height", "value": 3.0, "unit": "m", "expr": None,
            "inputs": [], "sourceRef": "studio:intent", "lockAuthority": None,
            "epistemicStatus": "declared",
        }]},
        target_ids=("wall-07",), producer_ids=("wall",),
        editable_fields=("height",) if tier == "scalar" else ("height", "thickness"),
        included_refs=("entity:wall-07", "entity:wall-08", "parameter:wall-height"),
    )


def _answer(*, edit=None, utterance=None, status="compiled", refs=()):
    return {
        "status": status, "targetComponentId": "facade", "elementId": "wall-07",
        "utterance": utterance, "semanticEdit": edit, "why": "Requested change.",
        "question": None, "contextRefs": list(refs),
    }


def _edit():
    fields = _wall_fields()
    fields["params"]["thickness"] = 0.3
    return {
        "summary": "Increase the wall height and thickness.",
        "entities": [{
            "entity_id": "wall-07", "schema": "Element@1", "parent_id": "facade",
            "basis_refs": ["studio:intent"], "fields": fields,
        }],
        "parameters": [{
            "key": "wall-height", "value": 4.0, "unit": "m", "expr": None,
            "inputs": [], "epistemic_status": "declared", "source_ref": "studio:intent",
        }],
        "relations": [], "removeEntityIds": [], "removeParameterKeys": [],
        "removeRelationIds": [], "protected": ["entity:wall-08"],
        "kept": ["Preserve the adjacent wall."],
    }


def _strict_answer():
    """Provider fixture includes the explicit nulls strict output requires."""
    answer = _answer(edit=_edit())
    fields = answer["semanticEdit"]["entities"][0]["fields"]
    fields.update(name=None, label=None, note=None)
    fields["references"].update(top=None, support=None)
    fields["references"]["line"]["face"] = None
    return answer


class IntentRequestSchemaTests(unittest.TestCase):
    def schema(self, context):
        return request_schema(context, full_schema=response_schema(strict=False))

    def assertRejected(self, answer, context):
        with self.assertRaises(ValueError):
            validate_request_answer(answer, context, self.schema(context))

    def test_scalar_accepts_only_the_named_field_and_writable_target(self):
        context = _context("scalar")
        validate_request_answer(_answer(utterance="set height to 4"), context, self.schema(context))
        for utterance in ("set thickness to 0.3", "invent a new wall", "set mystery to 4"):
            with self.subTest(utterance=utterance):
                self.assertRejected(_answer(utterance=utterance), context)
        self.assertRejected(_answer(edit=_edit()), context)
        wrong_target = _answer(utterance="set height to 4")
        wrong_target["elementId"] = "wall-08"
        self.assertRejected(wrong_target, context)
        wrong_parent = _answer(utterance="set height to 4")
        wrong_parent["targetComponentId"] = "another-facade"
        self.assertRejected(wrong_parent, context)

    def test_component_accepts_bound_numeric_edits_and_rejects_structural_edits(self):
        context = _context()
        schema = self.schema(context)
        validate_request_answer(_answer(edit=_edit()), context, schema)
        for key in ("removeEntityIds", "removeParameterKeys", "removeRelationIds"):
            edit = _edit()
            edit[key] = ["wall-08"]
            with self.subTest(removal=key):
                self.assertRejected(_answer(edit=edit), context)
        for target in ("wall-08", "new-wall"):
            edit = _edit()
            edit["entities"][0]["entity_id"] = target
            with self.subTest(target=target):
                self.assertRejected(_answer(edit=edit), context)
        for mutation, value in (("schema", "Type@1"), ("parent_id", "another-facade")):
            edit = _edit()
            edit["entities"][0][mutation] = value
            with self.subTest(field=mutation):
                self.assertRejected(_answer(edit=edit), context)
        edit = _edit()
        edit["parameters"][0]["key"] = "unbound-height"
        self.assertRejected(_answer(edit=edit), context)

    def test_component_numeric_scope_preserves_references_type_and_bindings(self):
        context = _context()
        mutations = {
            "different base level": lambda f: f["references"]["base"].update(level="level-03"),
            "different type": lambda f: f.update(type_ref="different-wall-type"),
            "binding replaced by literal": lambda f: f["params"].update(height=4),
            "different bound parameter": lambda f: f["params"].update(height="@another-height"),
            "new binding for literal": lambda f: f["params"].update(thickness="@another-thickness"),
            "removed numeric control": lambda f: f["params"].pop("thickness"),
            "unrequested opening": lambda f: f["params"].update(openings=[{
                "opening_id": "new-window", "kind": "window", "width": 1.0, "sill": 1.0, "head": 2.0,
            }]),
        }
        for label, mutate in mutations.items():
            edit = _edit()
            mutate(edit["entities"][0]["fields"])
            with self.subTest(change=label):
                self.assertRejected(_answer(edit=edit), context)

    def test_component_cannot_redefine_bound_parameter_units_or_expression(self):
        context = _context()
        for key, value in (("unit", "mm"), ("expr", "2 * other"), ("inputs", ["other"]), ("source_ref", "invented:source")):
            edit = _edit()
            edit["parameters"][0][key] = value
            with self.subTest(parameter_field=key):
                self.assertRejected(_answer(edit=edit), context)
        locked = deepcopy(context.sheet)
        locked["parameters"][0]["lockAuthority"] = "architect"
        self.assertRejected(_answer(edit=_edit()), replace(context, sheet=locked))

    def test_omitted_outer_metadata_keeps_the_existing_semantic_merge_behavior(self):
        context = _context()
        sheet = deepcopy(context.sheet)
        sheet["elements"][0]["authoredContext"] = {"name": "Courtyard wall", "custom_finish": "brick"}
        edit = _edit()
        del edit["entities"][0]["fields"]["type_ref"]
        # The existing component authoring owner merges outer fields. Their
        # omission preserves them; params and references are still complete.
        context = replace(context, sheet=sheet)
        validate_request_answer(_answer(edit=edit), context, self.schema(context))

    def test_component_numeric_edit_preserves_evidence_and_parameter_status(self):
        context = _context()
        for refs in ([], ["invented:source"]):
            edit = _edit()
            edit["entities"][0]["basis_refs"] = refs
            with self.subTest(basis_refs=refs):
                self.assertRejected(_answer(edit=edit), context)
        edit = _edit()
        edit["parameters"][0]["epistemic_status"] = "hypothesis"
        self.assertRejected(_answer(edit=edit), context)
        # Preserve the actual prior status, including an authored hypothesis.
        sheet = deepcopy(context.sheet)
        sheet["parameters"][0]["epistemicStatus"] = "hypothesis"
        context = replace(context, sheet=sheet)
        self.assertRejected(_answer(edit=_edit()), context)
        validate_request_answer(_answer(edit=edit), context, self.schema(context))

    def test_component_cannot_answer_a_multi_field_request_with_one_scalar(self):
        self.assertRejected(_answer(utterance="set height to 4"), _context())

    def test_request_envelope_rejects_missing_or_competing_compiled_edits(self):
        self.assertRejected(_answer(), _context("scalar"))
        self.assertRejected(_answer(utterance="set height to 4", edit=_edit()), _context())

    def test_context_supplement_is_bounded_and_cannot_smuggle_an_edit(self):
        context = _context("scalar")
        supplement = _answer(status="needs_context", refs=("entity:level-03",))
        validate_request_answer(supplement, context, self.schema(context))
        self.assertRejected(_answer(status="needs_context"), context)
        self.assertRejected(_answer(status="needs_context", refs=["entity:level-03"] * 17), context)
        self.assertRejected(_answer(status="needs_context", refs=("entity:level-03",), utterance="set height to 4"), context)
        self.assertRejected(_answer(utterance="set height to 4", refs=("entity:level-03",)), context)

    def test_design_schema_retains_new_entities_and_removals(self):
        context = _context("design")
        edit = _edit()
        edit["entities"][0]["entity_id"] = "new-wall"
        edit["removeEntityIds"] = ["wall-08"]
        edit["parameters"][0]["key"] = "new-height"
        validate_request_answer(_answer(edit=edit), context, self.schema(context))

    def test_unknown_fields_and_invalid_producer_values_fail_before_authoring(self):
        context = _context("design")
        unknown = _answer(edit=_edit())
        unknown["extra"] = "instructions"
        self.assertRejected(unknown, context)
        for key, value in (("producer", "invented-kernel"), ("arbitrary", "value")):
            answer = _answer(edit=_edit())
            answer["semanticEdit"]["entities"][0]["fields"][key] = value
            with self.subTest(field=key):
                self.assertRejected(answer, context)

    def test_factoring_preserves_strict_provider_validation_for_valid_and_invalid_answers(self):
        for tier in ("scalar", "component", "design"):
            with self.subTest(tier=tier):
                schema = self.schema(_context(tier))
                original = deepcopy(schema)
                strict = _strict_response_schema(schema)
                factored = provider_schema(schema, _strict_response_schema)
                Draft202012Validator.check_schema(factored)
                self.assertEqual(schema, original)
                ordinary = Draft202012Validator(strict)
                compact = Draft202012Validator(factored)
                samples = [_answer(utterance="set height to 4"), _strict_answer(),
                           _answer(status="needs_context", refs=("entity:level-03",))]
                for label in ("unknown field", "invalid reference", "invalid dimensions", "new target", "invalid status", "invalid count", "missing null"):
                    invalid = _strict_answer()
                    fields = invalid["semanticEdit"]["entities"][0]["fields"]
                    if label == "unknown field":
                        fields["unrecognized"] = "ignored?"
                    elif label == "invalid reference":
                        fields["params"]["height"] = "not-a-parameter-binding"
                    elif label == "invalid dimensions":
                        fields["references"]["line"]["inward"] = [1, 0, 0]
                    elif label == "new target":
                        invalid["elementId"] = "new-wall"
                    elif label == "invalid status":
                        invalid["status"] = "approve"
                    elif label == "invalid count":
                        fields["params"]["openings"] = [{
                            "opening_id": "bad-window", "component_id": None, "kind": "window",
                            "shape": None, "along": 1, "at": None, "width": 1, "sill": 1, "head": 2,
                            "spring_height": None, "count": 0, "step": None, "type_id": None,
                            "interface_ref": None,
                        }]
                    else:
                        del fields["name"]
                    samples.append(invalid)
                for sample in samples:
                    self.assertEqual(ordinary.is_valid(sample), compact.is_valid(sample))
                self.assertEqual(compact.is_valid(samples[0]), tier != "component")
                if tier != "scalar":
                    self.assertTrue(compact.is_valid(samples[1]))
                self.assertFalse(compact.is_valid(samples[3]))
                self.assertFalse(compact.is_valid(samples[4]))
                self.assertFalse(compact.is_valid(samples[5]))
                self.assertFalse(compact.is_valid(samples[-2]))
                self.assertFalse(compact.is_valid(samples[-1]))
                if tier == "design":
                    self.assertLess(len(json.dumps(factored)), len(json.dumps(strict)))


if __name__ == "__main__":
    unittest.main()
