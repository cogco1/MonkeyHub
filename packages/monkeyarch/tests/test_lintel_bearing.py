"""Declared lintel envelope coverage, without a CAD or structural-capacity claim."""
from __future__ import annotations

import copy
import json
import unittest

from archflow.state.state_record import Entity, Relation, StateRecord, StateRecordError, ValidatorBinding
from monkeyarch.capabilities.relation_checks import check_relations


PARAMETERS = {
    "opening_object_id": "opening-window",
    "lintel_object_id": "lintel-window",
    "span_axis": "x",
    "minimum_bearing_m": 0.15,
}
ENTITIES = (
    Entity("wall", "Element@1", {"producer": "wall"}),
    Entity("lintel", "Element@1", {"producer": "beam"}),
)
BOUNDS = {
    "opening-window": ([1.0, 0.9, 0.0], [3.0, 2.1, 0.4]),
    "lintel-window": ([0.85, 2.1, 0.05], [3.15, 2.3, 0.35]),
    "whole-wall": ([-100.0, -100.0, -100.0], [100.0, 100.0, 100.0]),
}
OBJECTS = {"wall": ["whole-wall", "opening-window"], "lintel": ["lintel-window"]}


def _relation(*, parameters=None, tolerance=None, kind="dependency") -> Relation:
    return Relation("opening-needs-lintel", kind, "wall", "lintel",
                    validator=ValidatorBinding("lintel_minimum_bearing", tolerance=tolerance),
                    parameters=dict(PARAMETERS if parameters is None else parameters))


def _report(*, relation=None, bounds=None, objects=None):
    record = StateRecord("detail", "run-1", ENTITIES, relations=(relation or _relation(),))
    return check_relations(record, bounds=BOUNDS if bounds is None else bounds,
                           objects_by_element=OBJECTS if objects is None else objects)


class LintelBearingTests(unittest.TestCase):
    def test_exact_opening_has_150_mm_each_side_without_using_whole_wall(self) -> None:
        source = copy.deepcopy(BOUNDS)
        report = _report(bounds=source)
        check = report.checks[0]
        self.assertEqual(check.status, "held")
        self.assertTrue(report.held and report.fully_checked)
        self.assertAlmostEqual(check.measured["left_bearing_m"], 0.15)
        self.assertAlmostEqual(check.measured["right_bearing_m"], 0.15)
        self.assertAlmostEqual(check.measured["transverse_overlap_m"], 0.3)
        self.assertEqual(check.measured["vertical_gap_m"], 0.0)
        self.assertEqual(check.tolerance, 0.001)
        self.assertIn("structural capacity are not measured", check.detail)
        self.assertEqual(source, BOUNDS)

    def test_z_span_uses_x_for_transverse_overlap_and_y_for_height(self) -> None:
        bounds = {key: ([low[2], low[1], low[0]], [high[2], high[1], high[0]])
                  for key, (low, high) in BOUNDS.items()}
        report = _report(relation=_relation(parameters={**PARAMETERS, "span_axis": "z"}), bounds=bounds)
        check = report.checks[0]
        self.assertEqual(check.status, "held")
        self.assertAlmostEqual(check.measured["left_bearing_m"], 0.15)
        self.assertAlmostEqual(check.measured["right_bearing_m"], 0.15)
        self.assertAlmostEqual(check.measured["transverse_overlap_m"], 0.3)

    def test_either_short_end_violates_with_the_measured_side_named(self) -> None:
        for side, index, value in (("left", 0, 0.9), ("right", 1, 3.1), ("right", 1, 2.9)):
            with self.subTest(side=side, value=value):
                bounds = copy.deepcopy(BOUNDS)
                bounds["lintel-window"][index][0] = value
                report = _report(bounds=bounds)
                self.assertEqual(report.checks[0].status, "violated")
                self.assertFalse(report.held)
                self.assertTrue(report.fully_checked)
                self.assertIn(f"{side} bearing", report.checks[0].detail)
                self.assertLess(report.checks[0].measured[f"{side}_bearing_m"], 0.15)

    def test_tolerance_applies_to_bearing_and_vertical_alignment(self) -> None:
        bounds = copy.deepcopy(BOUNDS)
        bounds["lintel-window"][0][0] = 0.8505
        bounds["lintel-window"][0][1] += 0.0005
        self.assertEqual(_report(bounds=bounds).checks[0].status, "held")
        check = _report(relation=_relation(tolerance=0.0001), bounds=bounds).checks[0]
        self.assertEqual(check.status, "violated")
        self.assertIn("left bearing", check.detail)
        self.assertIn("lintel bottom", check.detail)

    def test_zero_required_bearing_is_allowed(self) -> None:
        bounds = copy.deepcopy(BOUNDS)
        bounds["lintel-window"][0][0] = 1.0
        bounds["lintel-window"][1][0] = 3.0
        check = _report(relation=_relation(parameters={**PARAMETERS, "minimum_bearing_m": 0}, tolerance=0), bounds=bounds).checks[0]
        self.assertEqual(check.status, "held")
        self.assertEqual(check.measured["left_bearing_m"], 0.0)
        self.assertEqual(check.measured["right_bearing_m"], 0.0)

    def test_lintel_above_or_below_opening_top_violates(self) -> None:
        for shift in (-0.01, 0.01):
            with self.subTest(shift=shift):
                bounds = copy.deepcopy(BOUNDS)
                for corner in bounds["lintel-window"]:
                    corner[1] += shift
                check = _report(bounds=bounds).checks[0]
                self.assertEqual(check.status, "violated")
                self.assertAlmostEqual(check.measured["vertical_gap_m"], shift)
                self.assertIn("lintel bottom", check.detail)

    def test_transverse_separation_or_edge_touch_does_not_count_as_overlap(self) -> None:
        for near in (0.4, 0.5):
            with self.subTest(near=near):
                bounds = copy.deepcopy(BOUNDS)
                bounds["lintel-window"][0][2] = near
                bounds["lintel-window"][1][2] = near + 0.3
                check = _report(bounds=bounds).checks[0]
                self.assertEqual(check.status, "violated")
                self.assertLessEqual(check.measured["transverse_overlap_m"], 0.0)
                self.assertIn("no positive transverse overlap", check.detail)

    def test_missing_named_bounds_never_fall_back_to_another_object(self) -> None:
        for missing in ("opening-window", "lintel-window"):
            with self.subTest(missing=missing):
                bounds = {key: value for key, value in BOUNDS.items() if key != missing}
                bounds["other-opening"] = BOUNDS["opening-window"]
                objects = {**OBJECTS, "wall": [*OBJECTS["wall"], "other-opening"]}
                report = _report(bounds=bounds, objects=objects)
                self.assertEqual(report.checks[0].status, "unchecked")
                self.assertFalse(report.fully_checked)
                self.assertIn(missing, report.checks[0].detail)
                self.assertEqual(report.checks[0].measured, {})

    def test_wrong_or_missing_ownership_is_unchecked(self) -> None:
        for objects in ({}, {"wall": ["whole-wall"], "lintel": ["lintel-window", "opening-window"]},
                        {"wall": ["opening-window", "lintel-window"], "lintel": []}):
            with self.subTest(objects=objects):
                report = _report(objects=objects)
                self.assertEqual(report.checks[0].status, "unchecked")
                self.assertFalse(report.fully_checked)
                self.assertIn("not owned", report.checks[0].detail)

    def test_incomplete_nonfinite_reversed_or_degenerate_bounds_are_unchecked(self) -> None:
        invalid = (None, [], ([1, 2], [3, 4, 5]), ([1, 2, 3],),
                   ([1, 2, 3], [1, 4, 5]), ([3, 2, 3], [1, 4, 5]),
                   ([float("nan"), 2, 3], [4, 5, 6]), ([1, 2, 3], [float("inf"), 5, 6]),
                   ([True, 2, 3], [4, 5, 6]), (["1", 2, 3], [4, 5, 6]),
                   ({"x": 1, "y": 2, "z": 3}, [4, 5, 6]), ([1, 2, 3], [10**400, 5, 6]))
        for object_id in ("opening-window", "lintel-window"):
            for value in invalid:
                with self.subTest(object_id=object_id, value=value):
                    report = _report(bounds={**BOUNDS, object_id: value})
                    self.assertEqual(report.checks[0].status, "unchecked")
                    self.assertFalse(report.fully_checked)
                    self.assertIn(object_id, report.checks[0].detail)
                    self.assertEqual(len(report.digest), 64)

    def test_overflowing_measurements_are_unchecked_and_remain_serializable(self) -> None:
        bounds = {**BOUNDS,
                  "opening-window": ([1e308, 0.9, 0], [1.1e308, 2.1, 0.4]),
                  "lintel-window": ([-1e308, 2.1, 0.05], [1.2e308, 2.3, 0.35])}
        report = _report(bounds=bounds)
        self.assertEqual(report.checks[0].status, "unchecked")
        self.assertFalse(report.fully_checked)
        json.dumps(report.to_dict(), allow_nan=False)


class LintelBearingContractTests(unittest.TestCase):
    def test_missing_extra_and_invalid_parameters_are_refused(self) -> None:
        invalid = [{key: value for key, value in PARAMETERS.items() if key != missing} for missing in PARAMETERS]
        invalid += [{**PARAMETERS, "unmeasured": True}, {**PARAMETERS, "opening_object_id": "lintel-window"}]
        invalid += [{**PARAMETERS, key: value} for key, values in (
            ("opening_object_id", (None, "", "not/a/local/id")),
            ("lintel_object_id", (None, "", 1)),
            ("span_axis", ("y", "X", None, ["x"])),
            ("minimum_bearing_m", (-0.1, float("nan"), float("inf"), True, "0.15", None, 10**400)),
        ) for value in values]
        for parameters in invalid:
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    _relation(parameters=parameters)

    def test_relation_requires_dependency_kind_and_tolerance_binding(self) -> None:
        with self.assertRaisesRegex(StateRecordError, "requires kind dependency"):
            _relation(kind="support")
        with self.assertRaisesRegex(StateRecordError, "takes a tolerance"):
            ValidatorBinding("lintel_minimum_bearing", interval_m=(0, 0.15))

    def test_new_relation_and_record_roundtrip_preserve_exact_values(self) -> None:
        relation = _relation(parameters={**PARAMETERS, "minimum_bearing_m": 0}, tolerance=0.002)
        relation_json = json.loads(json.dumps(relation.to_dict()))
        restored = Relation.from_dict(relation_json)
        self.assertEqual(restored.to_dict(), relation_json)
        self.assertIs(type(restored.parameters["minimum_bearing_m"]), int)
        self.assertEqual(restored.validator.to_dict(), {"check_kind": "lintel_minimum_bearing", "tolerance": 0.002, "interval_m": None})
        record = StateRecord("detail", "run-1", ENTITIES, relations=(relation,))
        restored_record = StateRecord.from_dict(json.loads(json.dumps(record.to_dict())))
        self.assertEqual(restored_record.to_dict(), record.to_dict())
        self.assertEqual(restored_record.digest, record.digest)

    def test_retained_relations_and_validator_values_are_unchanged(self) -> None:
        for validator in (None, ValidatorBinding("support_contact"), ValidatorBinding("support_contact", tolerance=0.003),
                          ValidatorBinding("clearance_interval", interval_m=(0.1, 0.3))):
            with self.subTest(validator=validator):
                relation = Relation("old-relation", "support", "wall", "lintel", validator=validator,
                                    parameters={"retained_integer": 1, "retained_list": [0, 0.25, None]})
                raw = json.loads(json.dumps(relation.to_dict()))
                self.assertEqual(Relation.from_dict(raw).to_dict(), raw)
                if validator is not None:
                    self.assertEqual(ValidatorBinding.from_dict(validator.to_dict()).to_dict(), validator.to_dict())


if __name__ == "__main__":
    unittest.main()
