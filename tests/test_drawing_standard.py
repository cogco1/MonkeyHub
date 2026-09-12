"""Observable drawing consistency: two panels, one dimension chain and a schedule."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from monkeydiagram.documentation.drawings import compile_drawing_state, validate_drawing_state


def drawing_fixture() -> dict:
    dimensions = []
    for name, start, end, role in (
        ("overall", 0, 1200, "overall"),
        ("left", 0, 450, "component"),
        ("right", 450, 1200, "component"),
    ):
        dimensions.append({
            "id": name, "view_id": "front", "axis": "x", "datum": "origin-left",
            "start": start, "end": end, "value": end - start, "role": role,
            "certainty": "CONFIRMED", "representation": "ACTUAL", "actions": [],
            "use": "control", "source": {"record_id": "record-1", "parameter": name},
        })
    return {
        "source": {"project_id": "test-project", "record_id": "record-1", "run_id": "candidate-1"},
        "project": {"name": "Two-panel test", "reviewed_by": None},
        "sheets": [
            {"number": "A01", "title": "Arrangement", "purpose": "Overall coordination",
             "size_mm": [420, 297], "printable_rect_mm": [10, 10, 410, 287],
             "view_ids": ["front"], "primitive_bounds_mm": [[15, 15, 405, 280]]},
            {"number": "A02", "title": "Schedules", "purpose": "Material and interface review",
             "size_mm": [420, 297], "printable_rect_mm": [10, 10, 410, 287],
             "view_ids": ["schedule"]},
        ],
        "views": [
            {"id": "front", "sheet": "A01", "number": "01", "name": "Front elevation",
             "drawing_type": "GENERAL_DESIGN_INTENT", "purpose": "Overall panel arrangement",
             "scale": 10, "scale_group": "elevations", "object_ids": ["panel-left", "panel-right"],
             "bounds_mm": [20, 30, 200, 170], "information": ["overall_form", "principal_dimensions"]},
            {"id": "schedule", "sheet": "A02", "number": "01", "name": "Components and responsibilities",
             "drawing_type": "SCHEDULE_RESPONSIBILITY", "purpose": "Coordinate materials and site interface",
             "scale": None, "scale_group": None, "object_ids": [], "bounds_mm": [20, 30, 400, 275],
             "information": ["schedules", "responsibilities", "general_notes"]},
        ],
        "dimensions": dimensions,
        "dimension_chains": [{"id": "front-width", "dimension_ids": ["left", "right"], "overall_dimension_id": "overall"}],
        "references": [{"source_view_id": "front", "target_view_id": "schedule"}],
        "components": [
            {"object_id": "panel-left", "tag": "P01", "material_tag": "M01", "certainty": "CONFIRMED", "representation": "ACTUAL", "actions": []},
            {"object_id": "panel-right", "tag": "P02", "material_tag": "M01", "certainty": "CONFIRMED", "representation": "ACTUAL", "actions": []},
        ],
        "materials": [{"tag": "M01", "name": "Selected panel material", "thickness_mm": 18, "certainty": "ASSUMED"}],
        "schedules": [
            {"object_id": "panel-left", "component_tag": "P01", "material_tag": "M01", "view_id": "schedule"},
            {"object_id": "panel-right", "component_tag": "P02", "material_tag": "M01", "view_id": "schedule"},
        ],
        "notes": [{"id": "FIELD_VERIFY", "text": "Verify the existing opening before installation.", "view_ids": ["schedule"], "repeat_allowed": False}],
        "interfaces": [{"id": "opening", "object_ids": ["panel-left", "panel-right"],
                        "view_ids": ["front", "schedule"], "design_provides": ["Nominal panel arrangement"],
                        "field_verify": ["Existing clear opening"], "vendor_provides": [], "status": "UNRESOLVED"}],
        "revisions": [{"id": "R01", "date": "2026-09-06", "description": "Initial coordinated draft",
                       "issued_by": None, "issue_status": "draft", "changed_view_ids": ["front", "schedule"]}],
    }


class DrawingStandardTests(unittest.TestCase):
    def setUp(self):
        config = Path(__file__).resolve().parents[1] / "monkeydiagram" / "documentation" / "drawing_standard_v0_1.json"
        self.standard = json.loads(config.read_text(encoding="utf-8"))
        self.state = drawing_fixture()

    def codes(self):
        return {finding.code for finding in validate_drawing_state(self.state, self.standard)}

    def assert_rejected(self, code):
        self.assertIn(code, self.codes())
        with self.assertRaises(ValueError):
            compile_drawing_state(self.state, self.standard)

    def test_coordinated_draft_with_unscaled_schedule_compiles_without_changing_source_status(self):
        self.assertEqual(validate_drawing_state(self.state, self.standard), ())
        output = compile_drawing_state(self.state, self.standard).to_dict()
        self.assertEqual(output["dimensions"][0]["value"], 1200)
        self.assertIsNone(output["views"][1]["scale"])
        self.assertEqual(output["materials"][0]["certainty"], "ASSUMED")
        self.assertEqual(output["interfaces"][0]["status"], "UNRESOLVED")
        self.assertIsNone(output["revisions"][0]["issued_by"])
        self.assertEqual(output["views"][0]["title"], "01  Front elevation  1:10")
        self.assertEqual(output["views"][1]["scale_label"], "NTS")
        self.assertEqual(output["references"][0]["label"], "01 / A02")

    def test_plan_does_not_change_when_caller_revises_its_input(self):
        plan = compile_drawing_state(self.state, self.standard)
        self.state["dimensions"][0]["value"] = 900
        exported = plan.to_dict()
        self.assertEqual(exported["dimensions"][0]["value"], 1200)
        exported["dimensions"][0]["value"] = 800
        self.assertEqual(plan.to_dict()["dimensions"][0]["value"], 1200)

    def test_all_user_preferred_scales_and_a_reasoned_exception_work(self):
        for scale in (100, 50, 20, 10, 5, 2, 1):
            with self.subTest(scale=scale):
                self.state["views"][0]["scale"] = scale
                self.assertEqual(self.codes(), set())
        self.state["views"][0]["scale"] = 25
        self.assert_rejected("INVALID_SCALE")
        self.state["views"][0]["scale_exception_reason"] = "Coordinate with the supplied 1:25 survey."
        self.assertEqual(self.codes(), set())

    def test_scale_is_not_removed_from_a_graphical_view(self):
        self.state["views"][0]["scale"] = None
        self.assert_rejected("SCALE_REQUIRED")

    def test_compared_views_cannot_silently_change_scale_to_fit(self):
        second = deepcopy(self.state["views"][0])
        second.update(id="rear", number="02", scale=20)
        self.state["views"].append(second)
        self.state["sheets"][0]["view_ids"].append("rear")
        self.assert_rejected("SCALE_GROUP_MISMATCH")

    def test_duplicate_sheet_and_view_numbers_are_rejected(self):
        self.state["sheets"].append(deepcopy(self.state["sheets"][0]))
        self.assert_rejected("DUPLICATE_ID")
        self.state = drawing_fixture()
        second = deepcopy(self.state["views"][0])
        second["id"] = "rear"
        self.state["views"].append(second)
        self.state["sheets"][0]["view_ids"].append("rear")
        self.assert_rejected("DUPLICATE_VIEW_NUMBER")

    def test_callout_and_sheet_placement_must_resolve(self):
        self.state["references"][0]["target_view_id"] = "missing-section"
        self.assert_rejected("INVALID_REFERENCE")
        self.state = drawing_fixture()
        self.state["views"][0]["sheet"] = "A02"
        self.assert_rejected("VIEW_SHEET_MISMATCH")

    def test_dimension_text_cannot_override_its_geometry_value(self):
        self.state["dimensions"][1]["value"] = 500
        self.assert_rejected("DIMENSION_VALUE_MISMATCH")

    def test_same_endpoint_duplicate_and_conflicting_values_are_distinguished(self):
        extra = deepcopy(self.state["dimensions"][1])
        extra["id"] = "left-again"
        self.state["dimensions"].append(extra)
        self.assert_rejected("DUPLICATE_DIMENSION")
        extra["value"] = 500
        self.assert_rejected("CONFLICTING_DIMENSION")

    def test_chain_checks_sum_endpoints_and_shared_datum(self):
        self.state["dimensions"][2].update(start=500, value=700)
        self.assert_rejected("CHAIN_SUM_MISMATCH")
        self.assertIn("CHAIN_ENDPOINT_MISMATCH", self.codes())
        self.state = drawing_fixture()
        self.state["dimensions"][2].update(start=500, end=1250)
        self.assertNotIn("CHAIN_SUM_MISMATCH", self.codes())
        self.assert_rejected("CHAIN_ENDPOINT_MISMATCH")
        self.state = drawing_fixture()
        self.state["dimensions"][2]["datum"] = "different-origin"
        self.assert_rejected("CHAIN_DATUM_MISMATCH")

    def test_unconfirmed_dimensions_are_usable_for_coordination_but_not_control(self):
        for certainty in ("ASSUMED", "UNRESOLVED"):
            with self.subTest(certainty=certainty):
                dimension = self.state["dimensions"][0]
                dimension.update(certainty=certainty, use="control", actions=["FIELD_VERIFY"])
                self.assert_rejected("UNCONFIRMED_CONTROL_DIMENSION")
                dimension["use"] = "coordination"
                self.assertEqual(self.codes(), set())

    def test_dimension_derivation_cannot_upgrade_explicit_source_certainty(self):
        self.state["dimensions"][1].update(certainty="ASSUMED", use="coordination")
        self.state["dimensions"][0]["source"] = {"dimension_ids": ["left", "right"]}
        self.assert_rejected("SOURCE_CERTAINTY_UPGRADE")
        self.state["dimensions"][0].update(certainty="ASSUMED", use="coordination")
        self.assertEqual(self.codes(), set())

    def test_factual_status_cannot_be_invented_or_weakened_through_configuration(self):
        self.state["components"][0]["certainty"] = "GEOMETRY_LOOKS_RIGHT"
        self.assert_rejected("INVALID_CERTAINTY")
        self.state = drawing_fixture()
        self.standard["StatusSystem"]["control_requires_certainty"] = "ASSUMED"
        self.assert_rejected("INVALID_STANDARD")

    def test_missing_status_and_malformed_geometry_return_findings(self):
        del self.state["dimensions"][0]["certainty"]
        self.assert_rejected("INVALID_SHAPE")
        self.state = drawing_fixture()
        self.state["views"][0]["bounds_mm"] = [1, 2, float("nan"), 4]
        self.assert_rejected("INVALID_SHAPE")

    def test_note_definition_and_repeat_permission_are_checked(self):
        self.state["notes"][0]["view_ids"].append("front")
        self.assert_rejected("REPEATED_NOTE")
        self.state["notes"][0]["repeat_allowed"] = True
        self.assertEqual(self.codes(), set())
        other = deepcopy(self.state["notes"][0])
        other["text"] = "Do not verify the opening."
        self.state["notes"].append(other)
        self.assert_rejected("NOTE_DEFINITION_CONFLICT")

    def test_schedule_rows_cannot_guess_or_reassign_component_material(self):
        self.state["schedules"][0]["material_tag"] = "M99"
        self.assert_rejected("SCHEDULE_MAPPING_MISMATCH")
        self.assertIn("INVALID_REFERENCE", self.codes())
        self.state = drawing_fixture()
        self.state["schedules"].pop()
        self.assert_rejected("SCHEDULE_MAPPING_COUNT")
        self.state = drawing_fixture()
        self.state["schedules"].append(deepcopy(self.state["schedules"][0]))
        self.assert_rejected("SCHEDULE_MAPPING_COUNT")

    def test_changing_note_id_does_not_bypass_repeat_permission(self):
        duplicate = deepcopy(self.state["notes"][0])
        duplicate.update(id="OTHER_ID", view_ids=["front"], text="Verify the existing opening\nbefore installation.")
        self.state["notes"].append(duplicate)
        self.assert_rejected("REPEATED_NOTE_TEXT")
        for note in self.state["notes"]:
            note["repeat_allowed"] = True
        self.assertEqual(self.codes(), set())

    def test_one_tag_cannot_name_two_objects(self):
        self.state["components"][1]["tag"] = "P01"
        self.assert_rejected("DUPLICATE_COMPONENT_TAG")

    def test_interface_requires_explicit_responsibilities_and_confirmation_status(self):
        del self.state["interfaces"][0]["vendor_provides"]
        self.assert_rejected("INVALID_SHAPE")
        self.state = drawing_fixture()
        self.state["interfaces"][0]["status"] = "APPROVED_BY_RENDERER"
        self.assert_rejected("INVALID_INTERFACE_STATUS")

    def test_revision_date_targets_and_real_issuer_are_checked(self):
        revision = self.state["revisions"][0]
        revision["date"] = "2026-02-30"
        self.assert_rejected("INVALID_REVISION_DATE")
        revision["date"] = "2026-09-06"
        revision["changed_view_ids"] = ["missing"]
        self.assert_rejected("INVALID_REFERENCE")
        revision["changed_view_ids"] = ["front"]
        revision["issue_status"] = "issued"
        self.assert_rejected("ISSUER_REQUIRED")
        revision["issued_by"] = "Named project issuer"
        self.assertEqual(self.codes(), set())

    def test_actual_primitive_bounds_and_view_bounds_must_fit_printable_area(self):
        self.state["sheets"][0]["primitive_bounds_mm"].append([409, 20, 411, 23])
        self.assert_rejected("OUTSIDE_PRINTABLE_AREA")
        self.state = drawing_fixture()
        self.state["views"][0]["bounds_mm"][0] = 9
        self.assert_rejected("OUTSIDE_PRINTABLE_AREA")

    def test_declared_information_is_checked_without_claiming_geometric_validation(self):
        self.state["views"][0]["information"] = ["overall_form", "full_internal_component_inventory"]
        self.assert_rejected("MISSING_INFORMATION")
        self.assertIn("FORBIDDEN_INFORMATION", self.codes())
        del self.state["views"][0]["information"]
        self.assertEqual(self.codes(), set())

    def test_project_specific_json_fields_are_preserved(self):
        self.state["views"][0]["section_cut"] = {"source_plane": [0, 0, 1, 600]}
        output = compile_drawing_state(self.state, self.standard).to_dict()
        self.assertEqual(output["views"][0]["section_cut"]["source_plane"], [0, 0, 1, 600])
        self.assertEqual(json.loads(json.dumps(output)), output)


if __name__ == "__main__":
    unittest.main()
