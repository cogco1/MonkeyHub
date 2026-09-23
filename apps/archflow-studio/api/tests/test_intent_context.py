"""Request reduction follows the real record graph without widening edits."""

from copy import deepcopy
import base64
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import archflow_studio_api  # noqa: F401
from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding
from archflow_studio_api.application.intent_agent import Selection, record_sheet
from archflow_studio_api.application import intent_context
from archflow_studio_api.application.intent_context import compile_context, compile_task_context, confirmed_stage_context, expand_context, control_unit, model_context
from archflow_studio_api.application.projection import _elements
from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_design_history import DesignHistoryFixture
from .study_fixture import fixture_evidence, fixture_png, fixture_research


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


class TaskContextTests(unittest.TestCase):
    def test_multiple_focuses_keep_upstream_geometry_and_conditions_readable(self):
        record, _ = fixture()
        record = replace(record, parameters=(
            Parameter("locked_origin", 0.3, "m", lock_authority="studio:intent"),
            Parameter("module", 0.6, "m", expr="2 * locked_origin"),
            *record.parameters[1:],
        ))
        sheet = sheet_of(record, Selection(None, None))
        context = compile_task_context("Improve the relation between these openings", sheet, record=record,
                                       element_ids=("window-23", "window-24"))
        facts = model_context(context)
        self.assertEqual(context.target_ids, ("window-23", "window-24"))
        self.assertEqual(facts["focusElementIds"], ["window-23", "window-24"])
        self.assertIn("entity:wall-07", facts["readOnlyRefs"])
        self.assertIn("entity:level-02", facts["readOnlyRefs"])
        self.assertNotIn("entity:window-23", facts["readOnlyRefs"])
        locked = next(row for row in facts["parameters"] if row["key"] == "locked_origin")
        self.assertEqual(locked["lockAuthority"], "studio:intent")
        self.assertIn("global-keep", {row["obligation_id"] for row in facts["obligations"]})
        self.assertTrue(facts["coverage"]["complete"])

    def test_whole_design_pages_an_index_and_reads_omitted_geometry_without_expanding_focus(self):
        record, _ = fixture()
        record = replace(record, entities=record.entities + tuple(
            Entity(f"mass-{i:03}", "Element@1", {"producer": "prism", "component_id": "facade",
                   "params": {"height": 3.0}, "intent": "Authored design note " * 100}, "facade")
            for i in range(160)))
        sheet = sheet_of(record, Selection(None, None))
        original = deepcopy(sheet)
        context = compile_task_context("Explore an overall courtyard arrangement", sheet, record=record)
        facts = model_context(context)
        self.assertEqual(context.target_ids, ())
        self.assertNotIn("editTargets", facts)
        self.assertFalse(facts["coverage"]["complete"])
        self.assertGreater(facts["coverage"]["omittedCount"], 0)
        self.assertEqual(len(facts["index"]["items"]), 64)
        self.assertEqual(facts["index"]["nextOffset"], 64)
        self.assertLess(len(json.dumps(facts)), 50000)
        second = model_context(compile_task_context("Explore an overall courtyard arrangement", sheet, record=record,
                               context_refs=("entity:mass-159",), context_offset=64))
        self.assertIn("mass-159", {row["elementId"] for row in second["elements"]})
        self.assertEqual(second["focusElementIds"], [])
        self.assertEqual(second["readOnlyRefs"], [])
        self.assertTrue(set(row["ref"] for row in facts["index"]["items"]).isdisjoint(
                        row["ref"] for row in second["index"]["items"]))
        self.assertEqual(sheet, original)

    def test_missing_condition_is_explicit_and_unknown_supplement_is_refused(self):
        record, _ = fixture()
        record = replace(record, obligations=record.obligations + (
            DesignObligation("long-unresolved", "Unresolved evidence " * 3000, "studio:intent"),))
        sheet = sheet_of(record, Selection(None, None))
        facts = model_context(compile_task_context("Review the whole design", sheet, record=record))
        self.assertFalse(facts["coverage"]["complete"])
        self.assertEqual(facts["coverage"]["omittedConditionCount"], 1)
        self.assertIn("Incomplete coverage", facts["supplement"]["instruction"])
        with self.assertRaisesRegex(ValueError, "unknown exact reference"):
            compile_task_context("Review", sheet, record=record, context_refs=("entity:not-here",))

    def test_scalar_keeps_existing_projection_and_preflight_inputs(self):
        record, sheet = fixture()
        old = compile_context("set window-23 width to 1.2", sheet, record=record)
        new = compile_task_context("set window-23 width to 1.2", sheet, record=record, element_ids=("window-23",))
        self.assertEqual(new, old)


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
        # Inject a missing advertised contract independently of which producers
        # the owner supports today; the retained record still names its producer.
        record, sheet = fixture()
        sheet["producerSignatures"].pop("prism")
        context = compile_context("set this window width to 1.2 and height to 1.5", sheet, record=record)
        self.assertEqual(context.tier, "design")
        self.assertEqual(context.escalation, ("component_signature_unavailable",))

    def test_component_request_for_fields_the_signature_does_not_advertise_uses_design_tier(self):
        # The same tier, for the other reason: this producer does advertise a
        # signature, and width is not one of the parameters it names.
        for producer in ("prism", "loft"):
            with self.subTest(producer=producer):
                record, _ = fixture()
                record = replace(record, entities=tuple(
                    replace(item, fields={**item.fields, "producer": producer})
                    if item.entity_id in {"window-23", "window-type"} else item
                    for item in record.entities))
                sheet = sheet_of(record, Selection("facade", "window-23"))
                properties = sheet["producerSignatures"][producer]["parameters"]["properties"]
                self.assertNotIn("width", properties)
                if producer == "loft":
                    self.assertTrue({"profiles", "profile_size"}.issubset(properties))
                    self.assertNotIn("height", properties)
                context = compile_context("set this window width to 1.2 and height to 1.5", sheet, record=record)
                self.assertEqual(context.tier, "design")
                self.assertEqual(context.escalation, ("component_fields_not_advertised",))

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

    def test_local_design_keeps_dependencies_and_conditions_without_remote_state(self):
        record, sheet = fixture()
        sheet["constraints"] = ["Retain the agreed material specification"]
        context = compile_context("replace window-23 with a wider opening while keeping the host wall and daylight ratio", sheet, record=record)
        self.assertEqual(context.tier, "design")
        self.assertEqual(context.target_ids, ("window-23",))
        public = model_context(context)
        self.assertEqual(public["editTargets"], ["window-23"])
        self.assertEqual({row["elementId"] for row in public["elements"]}, {"window-23", "wall-07"})
        self.assertIn("parameter:module", context.included_refs)
        self.assertIn("Keep the opening daylight ratio", json.dumps(public))
        self.assertIn("Preserve agreed project limits", json.dumps(public))
        self.assertEqual(public["constraints"], sheet["constraints"])
        self.assertIn("remote-wall", {row["elementId"] for row in context.sheet["elements"]})
        self.assertNotIn("Unrelated wall finish", json.dumps(public))

    def test_named_keep_target_is_read_only_and_supplement_cannot_expand_writes(self):
        record, sheet = fixture()
        context = compile_context("replace window-23 while keeping wall-07", sheet, record=record)
        self.assertEqual(context.target_ids, ("window-23",))
        expanded = expand_context(context, sheet, ["entity:remote-wall"], record=record)
        self.assertEqual(expanded.target_ids, context.target_ids)
        self.assertEqual(model_context(expanded)["editTargets"], ["window-23"])
        self.assertIn("remote-wall", {row["elementId"] for row in model_context(expanded)["elements"]})
        self.assertNotIn("remote-wall", {row["elementId"] for row in model_context(context)["elements"]})

    def test_local_design_keeps_declared_downstream_dependency_without_a_relation(self):
        record, _ = fixture()
        remote = record.entity("remote-wall")
        remote = replace(remote, fields={**remote.fields, "host": "wall-07"})
        record = replace(record, entities=tuple(remote if row.entity_id == remote.entity_id else row for row in record.entities))
        context = compile_context("reconfigure wall-07 while keeping its base", sheet_of(record), record=record)
        self.assertEqual(context.target_ids, ("wall-07",))
        self.assertIn("entity:remote-wall", record.closure(("entity:wall-07",)))
        self.assertIn("remote-wall", {row["elementId"] for row in model_context(context)["elements"]})
        self.assertIn("Unrelated wall finish", json.dumps(model_context(context)))

    def test_clear_component_and_multiple_named_local_targets_are_supported(self):
        record, sheet = fixture()
        context = compile_context("reconfigure window-23 and window-24 while preserving daylight", sheet, record=record)
        self.assertEqual(context.target_ids, ("window-23", "window-24"))
        self.assertNotIn("remote-wall", {row["elementId"] for row in model_context(context)["elements"]})
        component = compile_context("reconfigure facade while keeping its material", sheet, record=record)
        self.assertEqual(set(component.target_ids), {"wall-07", "window-23", "window-24", "remote-wall"})

    def test_global_and_unanchored_design_does_not_claim_local_scope(self):
        record, sheet = fixture()
        for message in ("redesign the entire building", "add a window while keeping width 1.2", "set this wall height to 3.2", "change window-23 and improve circulation"):
            with self.subTest(message=message):
                context = compile_context(message, sheet, record=record)
                self.assertIsNone(context.design_sheet)
                self.assertNotIn("editTargets", model_context(context))

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


class ModelContextTests(unittest.TestCase):
    def context(self, record=None):
        if record is None:
            record, _ = fixture()
        return compile_context("set window-23 width to 1.4", sheet_of(record), record=record)

    def test_model_facts_do_not_mutate_or_disclose_private_validation_closure(self):
        context = self.context()
        before = deepcopy(context.sheet)
        public = model_context(context)
        self.assertEqual(context.sheet, before)
        self.assertIn("entity:wall-07", context.included_refs)
        self.assertIn("parameter:module", context.included_refs)
        self.assertIn("parameterBindings", context.sheet["elements"][1])
        text = json.dumps(public)
        for key in ("producer", "parameterBindings", "sourceRef", "basisRefs", "expr", "inputs", "lockAuthority", "tier", "expansionCount", "projectId", "semanticIds", "requestContext"):
            self.assertNotIn('"' + key + '"', text)
        self.assertNotIn("window-width", text)
        control = public["targets"][0]["controls"][0]
        self.assertEqual((control["field"], control["currentValue"], control["unit"]), ("width", 1.2, "m"))
        self.assertFalse(control["editable"])
        self.assertIn("derived", control["reason"])
        public["targets"][0]["controls"][0]["currentValue"] = 900
        self.assertEqual(context.sheet, before)

    def test_shared_impact_retains_direct_and_derived_consumers_without_graph_rows(self):
        record, _ = fixture(shared=True)
        remote = record.entity("remote-wall")
        remote = replace(remote, fields={**remote.fields, "params": {"height": "@dependent-size"}})
        record = replace(record, parameters=(*record.parameters, Parameter("dependent-size", 2.4, "m", inputs=("window-width",))),
                         entities=tuple(remote if row.entity_id == remote.entity_id else row for row in record.entities))
        public = model_context(self.context(record))
        impact = public["targets"][0]["controls"][0]["sharedImpact"]
        self.assertEqual(impact["affectedCount"], 3)
        self.assertEqual(impact["affectedObjects"], ["remote-wall", "window-23", "window-24"])
        self.assertNotIn("elements", public)
        self.assertNotIn("dependent-size", json.dumps(public))

    def test_unrelated_locks_and_their_constraints_do_not_grow_model_context(self):
        record, _ = fixture()
        initial = model_context(self.context(record))
        locks = tuple(Parameter(f"private-lock-{index}", 1, "m", lock_authority="review:private") for index in range(40))
        duties = tuple(DesignObligation(f"private-duty-{index}", "Unrelated locked decision", "studio:intent", subject_refs=(f"parameter:private-lock-{index}",)) for index in range(40))
        record = replace(record, parameters=(*record.parameters, *locks), obligations=(*record.obligations, *duties))
        context = self.context(record)
        self.assertIn("parameter:private-lock-39", context.included_refs)
        self.assertEqual(model_context(context), initial)

    def test_shared_dimension_used_in_a_reference_still_reports_affected_object(self):
        record, _ = fixture()
        remote = record.entity("remote-wall")
        remote = replace(remote, fields={**remote.fields, "references": {"base": {"offset_from": {"level": "level-02", "offset": "@window-width"}}}})
        record = replace(record, entities=tuple(remote if row.entity_id == remote.entity_id else row for row in record.entities))
        context = self.context(record)
        self.assertIn("entity:remote-wall", context.included_refs)
        impact = model_context(context)["targets"][0]["controls"][0]["sharedImpact"]
        self.assertEqual(impact["affectedCount"], 2)
        self.assertIn("remote-wall", impact["affectedObjects"])

    def test_selected_lock_is_a_design_fact_without_lock_owner_identity(self):
        record, _ = fixture()
        record = replace(record, parameters=tuple(replace(row, lock_authority="review:secret-owner") if row.key == "window-width" else row for row in record.parameters))
        public = model_context(self.context(record))
        control = public["targets"][0]["controls"][0]
        self.assertFalse(control["editable"])
        self.assertIn("locked", control["reason"])
        self.assertNotIn("secret-owner", json.dumps(public))

    def test_shared_control_on_two_fields_of_one_object_is_not_hidden(self):
        record, _ = fixture()
        window = record.entity("window-23")
        window = replace(window, fields={**window.fields, "params": {"width": "@window-width", "height": "@window-width"}})
        record = replace(record, entities=tuple(window if row.entity_id == window.entity_id else row for row in record.entities))
        impact = model_context(self.context(record))["targets"][0]["controls"][0]["sharedImpact"]
        self.assertEqual(impact["affectedCount"], 1)
        self.assertEqual(impact["coupledFieldsOnThisObject"], ["height"])

    def test_design_conditions_keep_meaning_without_validator_or_source_payload(self):
        record, _ = fixture()
        clearance = Relation("clearance-rule", "dependency", "wall-07", "window-23", validator=ValidatorBinding("clearance_interval", interval_m=(0.1, 0.3)))
        record = replace(record, relations=(*record.relations, clearance))
        public = model_context(self.context(record))
        text = json.dumps(public)
        self.assertIn("Keep the opening daylight ratio", text)
        self.assertIn("Preserve agreed project limits", text)
        self.assertIn("Maintain the declared support contact", text)
        self.assertNotIn("Unrelated wall finish", text)
        self.assertNotIn('"validator"', text)
        self.assertNotIn("source_ref", text)
        self.assertIn({"interval": [0.1, 0.3], "unit": "m"}, [fact["requiredClearance"] for fact in public["designFacts"] if "requiredClearance" in fact])

    def test_units_come_from_bindings_or_the_declared_wall_contract(self):
        context = self.context()
        row = next(item for item in context.sheet["elements"] if item["elementId"] == "window-23")
        self.assertEqual(control_unit(context, row, "width"), "m")
        self.assertIsNone(control_unit(context, row, "height"))
        self.assertEqual(control_unit(context, {"producer": "wall"}, "height"), "m")
        self.assertIsNone(control_unit(context, {"producer": "wall"}, "count"))
        other = replace(context, sheet={**context.sheet, "parameters": [{"key": "window-width", "unit": "mm"}]})
        self.assertEqual(control_unit(other, {**row, "producer": "wall"}, "width"), "mm")

    def test_supplement_is_projected_as_read_facts_without_new_actions(self):
        record, sheet = fixture()
        context = self.context(record)
        initial = model_context(context)
        expanded = expand_context(context, sheet, ["entity:remote-wall"], record=record)
        public = model_context(expanded)
        self.assertEqual(public["targets"], initial["targets"])
        self.assertIn("remote-wall", [fact["name"] for fact in public["dependencyFacts"]])
        self.assertIn("Unrelated wall finish", json.dumps(public))
        self.assertNotIn("producer", json.dumps(public))
        self.assertEqual(expanded.target_ids, context.target_ids)

    def test_component_exposes_only_requested_numeric_controls(self):
        record, _ = fixture()
        wall = record.entity("wall-07")
        wall = replace(wall, fields={**wall.fields, "producer": "wall", "params": {"height": 3.0, "thickness": 0.2}})
        record = replace(record, entities=tuple(wall if row.entity_id == wall.entity_id else row for row in record.entities))
        context = compile_context("set wall-07 height to 3.5 and thickness to 0.3", sheet_of(record, Selection("facade", "wall-07")), record=record)
        public = model_context(context)
        self.assertEqual([control["field"] for control in public["targets"][0]["controls"]], ["height", "thickness"])
        self.assertTrue(all(control["unit"] == "m" for control in public["targets"][0]["controls"]))
        self.assertNotIn("parameters", public)
        self.assertNotIn("relationships", public)

    def test_design_path_keeps_full_context_without_repeated_signatures(self):
        record, sheet = fixture()
        context = compile_context("reorganize the gallery", sheet, record=record)
        public = model_context(context)
        self.assertEqual(public["elements"], context.sheet["elements"])
        self.assertNotIn("producerSignatures", public)
        self.assertIn("producerSignatures", context.sheet)

    def test_closure_reads_each_row_once_however_wide_it_grows(self):
        """The dependency closure widens a set of ids; the rows do not change.

        A row re-read on every round is the same answer bought again, and the
        cost of it grows with the record rather than with the slice. What the
        closure keeps is asserted throughout this file; this asserts what it
        stops spending to keep it.
        """

        record, sheet = fixture(shared=True)
        rows = set()
        reads = []
        real = intent_context._named_refs

        def counting(value, known):
            rows.update(known)
            reads.append(id(value))
            return real(value, known)

        intent_context._named_refs = counting
        try:
            context = compile_context("set window-23 width to 1.4", sheet, record=record)
        finally:
            intent_context._named_refs = real
        # The slice still reaches the shared parameter's other consumer.
        self.assertIn("entity:window-24", context.included_refs)
        self.assertEqual(len(reads), len(set(reads)), "a row was read for its references more than once")
        self.assertLessEqual(len(reads), len(rows))


class ConfirmedStageContextTests(unittest.TestCase):
    def summary(self, accepted_record, current_record=None):
        accepted = SimpleNamespace(record=accepted_record, run=SimpleNamespace(run_id="accepted-run"),
                                   record_source="accepted-state", state_digest="a" * 64)
        current = accepted if current_record is None else SimpleNamespace(
            record=current_record, run=SimpleNamespace(run_id="candidate-run"),
            record_source="candidate-state", state_digest="b" * 64)
        stage = SimpleNamespace(label="Massing", branch_id="main", candidate_id="accepted-run",
                                record_ref=SimpleNamespace(uri="accepted-state"))
        return confirmed_stage_context(current, accepted, stage, stage_ref="accepted-stage")

    def test_accepted_conditions_are_named_without_claiming_whole_geometry_lock(self):
        record, _ = fixture()
        record = replace(record, parameters=(replace(record.parameters[0], lock_authority="studio:intent"),
                                             *record.parameters[1:]))
        summary = self.summary(record)
        self.assertTrue(summary["isSource"])
        self.assertEqual(summary["label"], "Massing")
        self.assertEqual(summary["lockedParameterKeys"], ["module"])
        self.assertIn("entity:local-reading", summary["retainedConditionRefs"])
        self.assertIn("obligation:global-keep", summary["retainedConditionRefs"])
        self.assertEqual(summary["changes"]["changedRefs"], [])
        self.assertEqual(summary["changes"]["needsReviewRefs"], [])
        self.assertIn("do not freeze whole geometry", " ".join(summary["limitations"]))
        # Rebinding exactly the same content does not endorse the new run.
        candidate = self.summary(record, replace(record, run_id="another-run"))
        self.assertFalse(candidate["isSource"])
        self.assertEqual(candidate["changes"]["changedRefs"], [])

    def test_upstream_parameter_change_reaches_declared_transitive_consumers_and_conditions(self):
        record, _ = fixture(shared=True)
        changed = replace(record, parameters=(replace(record.parameters[0], value=0.7), *record.parameters[1:]))
        summary = self.summary(record, changed)
        self.assertEqual(summary["changes"]["changedRefs"], ["parameter:module"])
        self.assertEqual(set(summary["changes"]["affectedRefs"]),
                         {"parameter:window-width", "entity:window-23", "entity:window-24"})
        review = summary["changes"]["needsReviewRefs"]
        self.assertIn("entity:local-reading", review)
        self.assertIn("obligation:local-keep", review)
        self.assertIn("obligation:global-keep", review)
        self.assertNotIn("entity:remote-wall", review)
        self.assertIn("not validation results", " ".join(summary["limitations"]))

    def test_reopened_reading_and_removed_relation_use_old_and_current_subjects(self):
        record, _ = fixture()
        changed = replace(record, entities=tuple(
            replace(item, fields={"subject_refs": ["entity:wall-07"], "note": "Reopen daylight condition"})
            if item.entity_id == "local-reading" else item for item in record.entities), relations=())
        summary = self.summary(record, changed)
        self.assertEqual(set(summary["changes"]["changedRefs"]),
                         {"entity:local-reading", "relation:opening-support"})
        self.assertIn("entity:wall-07", summary["changes"]["affectedRefs"])
        self.assertIn("entity:window-23", summary["changes"]["affectedRefs"])
        self.assertIn("obligation:local-keep", summary["changes"]["needsReviewRefs"])

    def test_unscoped_changed_condition_and_undeclared_stage_interfaces_remain_explicit(self):
        record, _ = fixture()
        changed = replace(record, entities=tuple(
            replace(item, fields={"note": "Structural clearance changed in an unlinked earlier stage"})
            if item.entity_id == "global-reading" else item for item in record.entities))
        summary = self.summary(record, changed)
        self.assertEqual(summary["changes"]["unresolvedImpactRefs"], ["entity:global-reading"])
        self.assertEqual(summary["changes"]["needsReviewRefs"], ["entity:global-reading", "obligation:global-keep"])
        self.assertIn("non-adjacent Stage interfaces absent from these records are not resolved", " ".join(summary["limitations"]))

    def test_removing_a_condition_scope_does_not_hide_unknown_impact_behind_old_subjects(self):
        record, _ = fixture()
        changed = replace(record, entities=tuple(
            replace(item, fields={"note": "The daylight condition now applies throughout the project"})
            if item.entity_id == "local-reading" else item for item in record.entities))
        summary = self.summary(record, changed)
        self.assertEqual(summary["changes"]["unresolvedImpactRefs"], ["entity:local-reading"])
        self.assertIn("entity:window-23", summary["changes"]["affectedRefs"])

    def test_summary_omissions_do_not_claim_complete_lock_or_condition_coverage(self):
        record, _ = fixture()
        record = replace(record, parameters=record.parameters + tuple(
            Parameter(f"locked-{i:03}", i, "m", lock_authority="studio:intent") for i in range(70)))
        changed = replace(record, parameters=tuple(replace(item, value=item.value + 1) for item in record.parameters))
        summary = self.summary(record, changed)
        self.assertEqual(len(summary["lockedParameterKeys"]), 64)
        self.assertEqual(summary["omittedCounts"]["lockedParameterKeys"], 6)
        self.assertEqual(summary["omittedCounts"]["changedRefs"], 9)


class ConfirmedStageContextRouteTests(DesignHistoryFixture):
    def context(self, client, *, run_id=REFERENCE_RUN_ID, stage_ref=None):
        state = client.get("/api/state", params={"run": run_id,
                                               **({"sourceStageRef": stage_ref} if stage_ref else {})})
        self.assertEqual(state.status_code, 200, state.text)
        response = client.post("/api/intents/context", json={
            "projectId": PROJECT_ID, "sourceRunId": run_id,
            "stateDigest": state.json()["stateDigest"],
            "utterance": "Continue the wall task from the confirmed massing result",
            **({"sourceStageRef": stage_ref} if stage_ref else {}),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_explicit_acceptance_and_cold_read_distinguish_stage_from_same_stage_candidate(self):
        self.assertIsNone(self.context(self.client)["confirmedStage"])
        initial = self.initialize()
        confirmed = self.context(self.client, stage_ref=initial["stageRef"])["confirmedStage"]
        self.assertEqual(confirmed["stageRef"], initial["stageRef"])
        self.assertEqual(confirmed["stateDigest"], initial["modelSource"]["stateDigest"])
        self.assertTrue(confirmed["isSource"])
        candidate_id = self.candidate_from(initial)
        with TestClient(create_app(self.settings)) as reopened:
            candidate = self.context(reopened, run_id=candidate_id)["confirmedStage"]
            self.assertEqual(candidate["stageRef"], initial["stageRef"])
            self.assertFalse(candidate["isSource"])
            self.assertIn("entity:portico-base", candidate["changes"]["changedRefs"])
            self.assertEqual(len(self.history(client=reopened)["stages"]), 1)
            accepted = self.accept(candidate_id, initial, client=reopened)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            stage = accepted.json()
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with TestClient(create_app(self.settings)) as reopened:
            confirmed = self.context(reopened, run_id=candidate_id)["confirmedStage"]
            self.assertEqual(confirmed["stageRef"], stage["stageRef"])
            self.assertEqual(confirmed["runId"], candidate_id)
            self.assertEqual(confirmed["stateDigest"], stage["modelSource"]["stateDigest"])
            self.assertTrue(confirmed["isSource"])
            self.assertEqual(confirmed["changes"]["changedRefs"], [])
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(self.repository.read_head(), self.initial_head)


class StudyEvidenceContextRouteTests(DesignHistoryFixture):
    def save_study(self, *, previous=None, research=None):
        if not hasattr(self, "study_document"):
            uploaded = self.client.post("/api/documents", json={
                "projectId": PROJECT_ID, "fileName": "synthetic-precedent.png", "mimeType": "image/png",
                "contentBase64": base64.b64encode(fixture_png("base")).decode("ascii"),
            })
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.study_document = uploaded.json()
        document = self.study_document
        saved = self.client.post("/api/studies", json={
            "projectId": PROJECT_ID, "studyId": "passage", "expectedPreviousRef": previous,
            "source": {"runId": document["runId"], "assetSha256": document["assetSha256"], "pageIndex": 0},
            "evidence": fixture_evidence(), "research": research if research is not None else fixture_research(),
        })
        self.assertEqual(saved.status_code, 201, saved.text)
        return saved.json()

    def context_request(self, studies=(), **extra):
        return {"projectId": PROJECT_ID, "sourceRunId": REFERENCE_RUN_ID,
                "stateDigest": self.state_digest, "utterance": "Continue the facade study",
                "elementId": "portico-base", "studyEvidence": list(studies), **extra}

    def test_cold_context_keeps_exact_old_prior_and_conditions_without_changing_design(self):
        first = self.save_study()
        changed = fixture_research(changed_context=True)
        changed["designPrior"]["statement"] = "A later revision must not replace the one requested."
        second = self.save_study(previous=first["ledgerRef"], research=changed)
        initial = self.initialize()
        requested = {"studyId": "passage", "ledgerRef": first["ledgerRef"]}
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with TestClient(create_app(self.settings)) as reopened:
            baseline = reopened.post("/api/intents/context", json=self.context_request(sourceStageRef=initial["stageRef"]))
            answer = reopened.post("/api/intents/context", json=self.context_request(
                [requested], sourceStageRef=initial["stageRef"]))
        self.assertEqual(answer.status_code, 200, answer.text)
        pack = answer.json()
        evidence = pack["studyEvidence"][0]
        self.assertTrue(evidence["completeness"]["complete"], evidence)
        self.assertEqual(evidence["ledgerRef"], first["ledgerRef"])
        self.assertNotEqual(evidence["ledgerRef"], second["ledgerRef"])
        self.assertEqual(evidence["designPrior"], first["research"]["designPrior"])
        self.assertEqual(evidence["compositionPattern"]["exceptions"], first["research"]["compositionPattern"]["exceptions"])
        self.assertEqual(evidence["source"], first["source"])
        self.assertLess(len(json.dumps(evidence).encode()), len(json.dumps(first).encode()))
        for key in ("source", "target", "keep", "request", "context", "scopedDecisions", "confirmedStage"):
            self.assertEqual(pack[key], baseline.json()[key], key)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertEqual(self.repository.read_head(), self.initial_head)

    def test_unrequested_studies_are_not_read(self):
        from archflow_studio_api.routes import intents
        self.save_study()
        with patch.object(intents, "read_study", side_effect=AssertionError("unrequested study read")):
            answer = self.client.post("/api/intents/context", json=self.context_request())
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["studyEvidence"], [])

    def test_invalid_project_study_or_revision_is_refused_not_rebound(self):
        saved = self.save_study()
        for request in (
            {"studyId": "other-study", "ledgerRef": saved["ledgerRef"]},
            {"studyId": "passage", "ledgerRef": saved["ledgerRef"].replace(PROJECT_ID, "other-project")},
            {"studyId": "passage", "ledgerRef": "not-a-record"},
        ):
            with self.subTest(request=request):
                answer = self.client.post("/api/intents/context", json=self.context_request([request]))
                self.assertEqual(answer.status_code, 422, answer.text)
                self.assertEqual(answer.json()["code"], "STUDY_LEDGER_REF_INVALID")

    def test_duplicate_or_unbounded_study_request_is_rejected_before_read(self):
        saved = self.save_study()
        selected = {"studyId": "passage", "ledgerRef": saved["ledgerRef"]}
        for studies in ([selected, selected], [selected] * 4, [{"studyId": "passage"}]):
            with self.subTest(studies=studies):
                answer = self.client.post("/api/intents/context", json=self.context_request(studies))
                self.assertEqual(answer.status_code, 422, answer.text)

    def test_budget_refusal_never_returns_a_prior_without_its_long_condition(self):
        research = fixture_research()
        research["designPrior"]["conditions"] = ["重要条件 " * 5000]
        saved = self.save_study(research=research)
        answer = self.client.post("/api/intents/context", json=self.context_request(
            [{"studyId": "passage", "ledgerRef": saved["ledgerRef"]}]))
        self.assertEqual(answer.status_code, 200, answer.text)
        evidence = answer.json()["studyEvidence"][0]
        self.assertFalse(evidence["completeness"]["complete"])
        self.assertEqual(evidence["completeness"]["reason"], "budget-exceeded")
        self.assertNotIn("designPrior", evidence)
        self.assertEqual(evidence["reopen"]["ledgerRef"], saved["ledgerRef"])


if __name__ == "__main__":
    unittest.main()
