"""Request reduction follows the real record graph without widening edits."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

import archflow_studio_api  # noqa: F401
from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding
from archflow_studio_api.application.intent_agent import Selection, record_sheet
from archflow_studio_api.application.intent_context import compile_context, expand_context
from archflow_studio_api.application.projection import _elements


def fixture(*, shared=False):
    record = StateRecord(
        "context-project", "run-context",
        entities=(
            Entity("building", "Component@1", {"semantic_kind": "building"}),
            Entity("facade", "Component@1", {"semantic_kind": "controlled-entry"}, "building"),
            Entity("level-02", "Level@1", {"role": "upper", "elevation": 3.0}),
            Entity("window-type", "Type@1", {"producer": "prism", "params": {"height": 1.0}}),
            Entity("wall-07", "Element@1", {"producer": "prism", "component_id": "facade", "references": {"base": {"level": "level-02"}}, "params": {"height": 3.0}}, "facade"),
            Entity("window-23", "Element@1", {"producer": "prism", "component_id": "facade", "host": "wall-07", "type_ref": "window-type", "params": {"width": "@window-width", "height": 1.0}}, "facade"),
            Entity("window-24", "Element@1", {"producer": "prism", "component_id": "facade", "params": {"width": "@window-width" if shared else 1.0, "height": 1.0}}, "facade"),
            Entity("remote-wall", "Element@1", {"producer": "prism", "component_id": "facade", "params": {"height": 3.0}}, "facade"),
            Entity("local-reading", "Reading@1", {"subject_refs": ["entity:window-23"], "note": "Keep the opening daylight ratio"}),
            Entity("remote-reading", "Reading@1", {"subject_refs": ["entity:remote-wall"], "note": "Unrelated wall finish"}),
            Entity("global-reading", "Reading@1", {"note": "Unscoped project preference"}),
        ),
        parameters=(
            Parameter("module", 0.6, "m"),
            Parameter("window-width", 1.2, "m", expr="2 * module"),
            Parameter("unrelated", 5.0, "m"),
        ),
        relations=(Relation("opening-support", "support", "wall-07", "window-23", validator=ValidatorBinding("support_contact", tolerance=0.001)),),
        obligations=(
            DesignObligation("local-keep", "Retain the declared opening support", "studio:intent", subject_refs=("entity:window-23",)),
            DesignObligation("global-keep", "Preserve agreed project limits", "studio:intent"),
            DesignObligation("remote-keep", "Keep remote wall", "studio:intent", subject_refs=("entity:remote-wall",)),
        ),
        basis_refs=("studio:intent",),
    )
    return record, sheet_of(record)


def sheet_of(record, selection=Selection("facade", "window-23")):
    elements, error = _elements(record)
    if error:
        raise AssertionError(error)
    projection = SimpleNamespace(project_id=record.project_id, record=record, components=None, elements=elements, parameters=record.parameters, honesty=())
    return record_sheet(projection, selection)


class IntentContextTests(unittest.TestCase):
    def test_scalar_keeps_host_level_type_parameter_chain_and_conditions(self):
        record, sheet = fixture()
        original = deepcopy(sheet)
        context = compile_context("set this window width to 1200 mm", sheet, record=record)
        self.assertEqual(context.tier, "scalar")
        self.assertEqual(context.target_ids, ("window-23",))
        self.assertEqual(context.editable_fields, ("width",))
        self.assertEqual(context.producer_ids, ("prism",))
        self.assertTrue({"entity:wall-07", "entity:level-02", "entity:window-type", "entity:facade", "entity:building", "parameter:module", "parameter:window-width"}.issubset(context.included_refs))
        self.assertEqual({row["elementId"] for row in context.sheet["elements"]}, {"window-23", "wall-07"})
        self.assertEqual({row["entity_id"] for row in context.sheet["readings"]}, {"local-reading", "global-reading"})
        self.assertEqual({row["obligation_id"] for row in context.sheet["obligations"]}, {"local-keep", "global-keep"})
        self.assertEqual(context.sheet["relationships"][0]["validator"]["check_kind"], "support_contact")
        self.assertEqual(sheet, original)
        self.assertLess(len(json.dumps(context.sheet)), len(json.dumps(sheet)) / 2)

    def test_changed_shared_parameter_includes_other_consumers_without_edit_grant(self):
        record, sheet = fixture(shared=True)
        context = compile_context("set window-23 width to 1.4", sheet, record=record)
        self.assertIn("entity:window-24", context.included_refs)
        self.assertNotIn("entity:remote-wall", context.included_refs)
        self.assertEqual(context.target_ids, ("window-23",))

    def test_multiple_current_numeric_fields_use_component_tier(self):
        record, _ = fixture()
        wall = record.entity("wall-07")
        wall = replace(wall, fields={**wall.fields, "producer": "wall", "params": {"height": 3.0, "thickness": 0.2}})
        record = replace(record, entities=tuple(wall if item.entity_id == wall.entity_id else item for item in record.entities))
        sheet = sheet_of(record, Selection("facade", "wall-07"))
        context = compile_context("set this wall thickness to 0.3 and height to 3.5", sheet, record=record)
        self.assertEqual(context.tier, "component")
        self.assertEqual(context.editable_fields, ("height", "thickness"))

    def test_component_request_without_advertised_producer_uses_design_tier(self):
        record, sheet = fixture()
        context = compile_context("set this window width to 1.2 and height to 1.5", sheet, record=record)
        self.assertEqual(context.tier, "design")
        self.assertEqual(context.escalation, ("component_signature_unavailable",))

    def test_chinese_scalar_request(self):
        record, sheet = fixture()
        context = compile_context("把这个窗的宽度改成1200毫米", sheet, record=record)
        self.assertEqual(context.tier, "scalar")

    def test_grid_role_dependencies_and_entity_keep_refs_are_retained(self):
        record, sheet = fixture()
        axis = Entity("axis-a", "GridAxis@1", {"role": "front", "origin": [0, 0, 0], "direction": [1, 0, 0]})
        window = record.entity("window-23")
        window = replace(window, fields={**window.fields, "references": {"axis": {"grid": "front"}}, "protected": ["entity:remote-wall"]})
        record = replace(record, entities=tuple(window if item.entity_id == window.entity_id else item for item in record.entities) + (axis,))
        sheet["frame"].append(axis.to_dict())
        context = compile_context("set window-23 width to 1.2", sheet, record=record)
        self.assertIn("entity:axis-a", context.included_refs)
        self.assertIn("entity:remote-wall", context.included_refs)
        target = next(row for row in context.sheet["elements"] if row["elementId"] == "window-23")
        self.assertEqual(target["authoredContext"]["protected"], ["entity:remote-wall"])

    def test_applicable_obligation_keeps_its_explicit_blocker(self):
        record, sheet = fixture()
        blocker = DesignObligation("remote-blocker", "Verify remote member first", "studio:intent", subject_refs=("entity:remote-wall",))
        local = replace(record.obligations[0], blocked_by=("obligation:remote-blocker",))
        record = replace(record, obligations=(local, *record.obligations[1:], blocker))
        context = compile_context("set window-23 width to 1.2", sheet, record=record)
        self.assertIn("obligation:remote-blocker", context.included_refs)
        self.assertIn("entity:remote-wall", context.included_refs)
        self.assertIn("remote-blocker", {row["obligation_id"] for row in context.sheet["obligations"]})

    def test_uncertain_or_structural_scope_keeps_complete_context(self):
        record, sheet = fixture()
        for message in (
            "reorganize the upper gallery", "set all window widths to 1.2",
            "set this wall height to 3.2", "set window-23 width to 1.2 and window-24 height to 1.4",
            "set this window width to 1.2 and improve circulation", "add a window while keeping width 1.2",
            "make this window nicer", "把这个窗改成1200",
            "set this window width to 1.2 and depth to 0.5", "set facade width to 1.2",
        ):
            with self.subTest(message=message):
                context = compile_context(message, sheet, record=record)
                self.assertEqual(context.tier, "design")
                self.assertEqual(context.sheet["elements"], compile_context("design", sheet, record=record).sheet["elements"])
                self.assertTrue(context.escalation)
        sheet["selection"]["elementId"] = None
        self.assertEqual(compile_context("set window width to 1.2", sheet, record=record).tier, "design")

    def test_record_is_required_to_claim_complete_dependency_context(self):
        _, sheet = fixture()
        self.assertEqual(compile_context("set window-23 width to 1.2", sheet).tier, "design")

    def test_expansion_adds_read_context_and_preserves_edit_boundary(self):
        record, sheet = fixture()
        context = compile_context("set window-23 width to 1.2", sheet, record=record)
        expanded = expand_context(context, sheet, ["entity:remote-wall"], record=record)
        self.assertIn("entity:remote-wall", expanded.included_refs)
        self.assertIn("entity:remote-reading", expanded.included_refs)
        self.assertEqual(expanded.target_ids, context.target_ids)
        self.assertEqual(expanded.editable_fields, context.editable_fields)
        self.assertEqual(expanded.producer_ids, context.producer_ids)
        self.assertEqual(expanded.tier, context.tier)
        self.assertEqual(expanded.expansion_count, 1)
        self.assertNotIn("entity:remote-wall", context.included_refs)

    def test_expansion_rejects_unknown_repeated_oversized_and_excessive_requests(self):
        record, sheet = fixture()
        context = compile_context("set window-23 width to 1.2", sheet, record=record)
        for refs in ([], ["entity:missing"], ["remote-wall"], ["entity:window-23"], ["entity:remote-wall"] * 17, "entity:remote-wall"):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                expand_context(context, sheet, refs, record=record)
        with self.assertRaisesRegex(ValueError, "limit"):
            expand_context(replace(context, expansion_count=2), sheet, ["entity:remote-wall"], record=record)


if __name__ == "__main__":
    unittest.main()
