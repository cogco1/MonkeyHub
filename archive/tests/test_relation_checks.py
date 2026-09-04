"""P102: relations are checked against realized geometry, never healed.

The first half measures what the producers actually built. The second half is
the vocabulary: ``state_record.CHECK_KINDS`` says what a relation may declare,
``CHECKERS`` says what the spine measures, and the two are the same three ids -
so a record can no longer declare a check that reports ``unchecked`` forever.
Those cases are hand-built bounds against a hand-built record, so a failure
names the measurement and not a run.

Bounds are program coordinates (x, y-up, z-plan): plan is x and z.
"""
from __future__ import annotations

import unittest

from archflow.adapters.cad_program import expected_object_bounds
from archflow.capabilities.relation_checks import CHECKERS, RelationCheckError, check_relations
from archflow.state.state_record import CHECK_KINDS, Entity, Relation, StateRecord, StateRecordError, ValidatorBinding
from archive.tests.test_cad_patch import _compile
from archive.tests.test_element_producers import _levels, _produce, _rows


def _record_and_bounds(rows=None, *, program_rows=None):
    rows = rows or _rows()
    produced, context = _produce(rows)
    program = _compile(program_rows or rows)
    bounds = {oid: (row["bbox_min"], row["bbox_max"]) for oid, row in expected_object_bounds(program).items()}
    entities = [Entity("portico-west", "Component@1", {"semantic_kind": "arrival", "intent": "west portico"})]
    entities += [Entity(l.level_id, "Level@1", {"role": l.role, "elevation": l.elevation}) for l in _levels().levels]
    entities += [Entity(r.element_id, "Element@1", {"component_id": r.component_id, "producer": r.producer}, parent_id="portico-west") for r in rows]
    relations = tuple(Relation(r.relation_id, r.kind, r.subject, r.object, datum_role=r.datum_id, propagation="revalidate",
                               validator=ValidatorBinding("support_contact", tolerance=0.001), parameters=dict(r.parameters))
                      for e in produced for r in e.relations)
    record = StateRecord("demo", "run-1", tuple(entities), (), relations)
    objects = {e.element_id: [o for op in p.operations for o in op.output_object_ids] for e, p in zip(rows, produced)}
    datums = {d.datum_id: context.datum_value(d.datum_id) for e in produced for d in e.datums}
    return record, bounds, objects, datums


class RelationCheckTests(unittest.TestCase):
    def test_contacts_by_construction_hold(self) -> None:
        record, bounds, objects, datums = _record_and_bounds()
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        self.assertTrue(report.held, [c.to_dict() for c in report.checks if c.status != "held"])
        self.assertEqual(report.to_dict()["counts"], {"held": 4, "violated": 0, "unchecked": 0})
        by_id = {c.relation_id: c for c in report.checks}
        self.assertAlmostEqual(by_id["columns-west-stands-on"].measured["level_elevation"], 3.57)
        self.assertAlmostEqual(by_id["columns-west-support-capitals-west"].measured["gap"], 0.0)

    def test_declared_engagement_is_measured_not_hidden(self) -> None:
        record, bounds, objects, datums = _record_and_bounds(_rows(engagement=0.02))
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        self.assertTrue(report.held)
        check = next(c for c in report.checks if c.relation_id == "columns-west-support-capitals-west")
        self.assertAlmostEqual(check.measured["engagement_depth"], 0.02)
        self.assertAlmostEqual(check.measured["subject_top"] - check.measured["object_bottom"], 0.02)

    def test_a_moved_column_violates_and_is_not_healed(self) -> None:
        record, bounds, objects, datums = _record_and_bounds(program_rows=_rows(column_height=7.0))    # record says 6.426, geometry says 7.0
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        self.assertFalse(report.held)
        violated = {c.relation_id: c for c in report.checks if c.status == "violated"}
        self.assertIn("columns-west-support-capitals-west", violated)                     # the datum no longer holds the face
        self.assertIn("datum", violated["columns-west-support-capitals-west"].detail)
        self.assertEqual(len(report.digest), 64)

    def test_gaps_fail_typed_and_an_unregistered_check_is_refused(self) -> None:
        record, bounds, objects, datums = _record_and_bounds()
        with self.assertRaises(RelationCheckError):
            check_relations(record, bounds={}, objects_by_element=objects, datum_values=datums)
        with self.assertRaises(StateRecordError) as raised:                              # nothing measures `alignment`, so the record refuses to declare it
            ValidatorBinding("alignment")
        for registered in CHECK_KINDS:
            self.assertIn(registered, str(raised.exception))
        other = StateRecord("demo", "run-1", record.entities, (), (Relation("s", "support", "capitals-west", "entablature-west"),))
        report = check_relations(other, bounds=bounds, objects_by_element=objects)
        self.assertEqual([c.status for c in report.checks], ["unchecked"])                # no validator bound
        self.assertTrue(report.held)                                                     # not violated ...
        self.assertFalse(report.fully_checked)                                           # ... but no green light either
        self.assertFalse(report.to_dict()["fully_checked"])


ELEMENTS = (
    Entity("wall-south", "Element@1", {"component_id": "envelope", "producer": "wall"}),
    Entity("window-south", "Element@1", {"component_id": "envelope", "producer": "opening"}),
    Entity("stair-run", "Element@1", {"component_id": "circulation", "producer": "stair"}),
)

WALL = ([0.0, 0.0, 0.0], [0.3, 3.0, 5.0])           # a 300 mm leaf, 3 m tall, 5 m long in plan-z


def _hand_built(*relations: Relation) -> StateRecord:
    return StateRecord("villa", "run-1", ELEMENTS, relations=relations)


def _report(relation: Relation, bounds, objects_by_element):
    return check_relations(_hand_built(relation), bounds=bounds, objects_by_element=objects_by_element)


class VocabularyTests(unittest.TestCase):
    def test_the_checker_table_is_the_accepted_vocabulary(self) -> None:
        self.assertEqual(set(CHECKERS), set(CHECK_KINDS))
        self.assertEqual(sorted(CHECK_KINDS), ["aperture_exists", "clearance_interval", "support_contact"])
        for kind, meaning in CHECK_KINDS.items():
            self.assertTrue(meaning.strip(), f"{kind} has no stated meaning")

    def test_a_bound_relation_is_never_unchecked_for_want_of_a_checker(self) -> None:
        bounds = {"obj-wall": WALL, "obj-window": ([0.0, 0.9, 1.0], [0.3, 2.1, 2.2])}
        objects = {"wall-south": ["obj-wall"], "window-south": ["obj-window"]}
        for kind, relation_kind in (("support_contact", "support"), ("clearance_interval", "clearance"), ("aperture_exists", "hosts_void")):
            validator = ValidatorBinding(kind, interval_m=(0.0, 1.0)) if kind == "clearance_interval" else ValidatorBinding(kind, tolerance=0.01)
            check = _report(Relation(f"r-{kind}", relation_kind, "wall-south", "window-south", validator=validator), bounds, objects).checks[0]
            self.assertEqual(check.check_kind, kind)
            self.assertIn(check.status, ("held", "violated"))                    # measured, never "no checker for this kind yet"


class ClearanceIntervalTests(unittest.TestCase):
    def _relation(self, low: float, high: float) -> Relation:
        return Relation("stair-clear-of-wall", "clearance", "wall-south", "stair-run",
                        validator=ValidatorBinding("clearance_interval", interval_m=(low, high)))

    def test_a_gap_inside_the_interval_holds(self) -> None:
        bounds = {"obj-wall": WALL, "obj-stair": ([1.5, 0.0, 1.0], [2.5, 3.0, 3.0])}
        report = _report(self._relation(1.0, 1.5), bounds, {"wall-south": ["obj-wall"], "stair-run": ["obj-stair"]})
        check = report.checks[0]
        self.assertEqual((check.check_kind, check.status), ("clearance_interval", "held"))
        self.assertAlmostEqual(check.measured["gap"], 1.2)                      # 1.5 - 0.3 on x, the largest per-axis separation
        self.assertIn("1.2000", check.detail)
        self.assertTrue(report.held and report.fully_checked)

    def test_a_gap_outside_the_interval_is_violated_and_the_detail_names_both(self) -> None:
        bounds = {"obj-wall": WALL, "obj-stair": ([1.5, 0.0, 1.0], [2.5, 3.0, 3.0])}
        check = _report(self._relation(0.0, 0.9), bounds, {"wall-south": ["obj-wall"], "stair-run": ["obj-stair"]}).checks[0]
        self.assertEqual(check.status, "violated")
        self.assertAlmostEqual(check.measured["gap"], 1.2)
        self.assertIn("1.2000", check.detail)
        self.assertIn("0.9000", check.detail)

    def test_overlapping_extents_measure_a_zero_gap(self) -> None:
        bounds = {"obj-wall": WALL, "obj-stair": ([0.2, 0.0, 1.0], [1.2, 3.0, 3.0])}
        check = _report(self._relation(0.9, 1.5), bounds, {"wall-south": ["obj-wall"], "stair-run": ["obj-stair"]}).checks[0]
        self.assertEqual((check.status, check.measured["gap"]), ("violated", 0.0))

    def test_an_element_without_objects_stays_unchecked_and_is_named(self) -> None:
        report = _report(self._relation(1.0, 1.5), {"obj-wall": WALL}, {"wall-south": ["obj-wall"]})
        check = report.checks[0]
        self.assertEqual(check.status, "unchecked")
        self.assertIn("stair-run", check.detail)
        self.assertTrue(report.held)                                            # unchecked is not a violation
        self.assertFalse(report.fully_checked)                                  # and never a green light


class ApertureExistsTests(unittest.TestCase):
    RELATION = Relation("wall-hosts-window", "hosts_void", "wall-south", "window-south",
                        validator=ValidatorBinding("aperture_exists", tolerance=0.01))

    def test_an_opening_inside_its_host_holds(self) -> None:
        bounds = {"obj-wall": WALL, "obj-window": ([0.0, 0.9, 1.0], [0.3, 2.1, 2.2])}
        check = _report(self.RELATION, bounds, {"wall-south": ["obj-wall"], "window-south": ["obj-window"]}).checks[0]
        self.assertEqual((check.check_kind, check.status), ("aperture_exists", "held"))
        self.assertIn("window-south", check.detail)

    def test_an_opening_beyond_the_host_in_plan_is_violated_and_the_detail_names_the_axis(self) -> None:
        bounds = {"obj-wall": WALL, "obj-window": ([0.0, 0.9, 4.5], [0.3, 2.1, 5.4])}
        check = _report(self.RELATION, bounds, {"wall-south": ["obj-wall"], "window-south": ["obj-window"]}).checks[0]
        self.assertEqual(check.status, "violated")
        self.assertAlmostEqual(check.measured["z_high_overrun"], 0.4)
        self.assertIn("plan", check.detail)
        self.assertIn("z", check.detail)

    def test_an_opening_taller_than_its_host_is_violated_vertically(self) -> None:
        bounds = {"obj-wall": WALL, "obj-window": ([0.0, 0.9, 1.0], [0.3, 3.6, 2.2])}
        check = _report(self.RELATION, bounds, {"wall-south": ["obj-wall"], "window-south": ["obj-window"]}).checks[0]
        self.assertEqual(check.status, "violated")
        self.assertAlmostEqual(check.measured["y_high_overrun"], 0.6)
        self.assertIn("vertically", check.detail)

    def test_an_opening_without_finite_geometry_is_violated_not_unchecked(self) -> None:
        bounds = {"obj-wall": WALL, "obj-window": ([0.0, 0.9, 1.0], [0.3, float("inf"), 2.2])}
        check = _report(self.RELATION, bounds, {"wall-south": ["obj-wall"], "window-south": ["obj-window"]}).checks[0]
        self.assertEqual(check.status, "violated")                              # the aperture was declared and is not there
        self.assertIn("window-south", check.detail)

    def test_a_host_without_objects_stays_unchecked_and_is_named(self) -> None:
        bounds = {"obj-window": ([0.0, 0.9, 1.0], [0.3, 2.1, 2.2])}
        check = _report(self.RELATION, bounds, {"window-south": ["obj-window"]}).checks[0]
        self.assertEqual(check.status, "unchecked")
        self.assertIn("wall-south", check.detail)


class ReportTests(unittest.TestCase):
    def test_a_relation_without_a_validator_is_still_unchecked(self) -> None:
        relation = Relation("stair-passes-wall", "allows_passage", "wall-south", "stair-run")
        check = _report(relation, {}, {}).checks[0]
        self.assertEqual((check.check_kind, check.status, check.detail), ("none", "unchecked", "no validator bound"))


if __name__ == "__main__":
    unittest.main()
