"""P102: relations are checked against realized geometry, never healed.

The first half measures what the producers actually built. The second half is
the vocabulary: ``state_record.CHECK_KINDS`` says what a relation may declare,
``CHECKERS`` says what the spine measures, and the two name the same ids -
so a record can no longer declare a check that reports ``unchecked`` forever.
Those cases are hand-built bounds against a hand-built record, so a failure
names the measurement and not a run.

Bounds are program coordinates (x, y-up, z-plan): plan is x and z.
"""
from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.adapters.cad_program import expected_object_bounds
from monkeyarch.capabilities.element_producers import ElementRow
from monkeyarch.capabilities.relation_checks import CHECKERS, RelationCheckError, check_relations
from archflow.state.state_record import CHECK_KINDS, Entity, Relation, StateRecord, StateRecordError, ValidatorBinding
from tests.test_cad_patch import _compile
from tests.test_element_producers import BASIS, PN, _levels, _produce, _rows


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


def _shifted(bounds, object_ids, axis: int, by: float) -> dict:
    """The same bounds with the named objects translated along one program axis (0 = x, 1 = y up, 2 = z)."""

    out = dict(bounds)
    for object_id in object_ids:
        low, high = (list(v) for v in bounds[object_id])
        low[axis] += by
        high[axis] += by
        out[object_id] = (low, high)
    return out


def _stair_row() -> ElementRow:
    """The real flight the producer builds: ten solid steps as one stepped solid along the west facade, standing on the piano nobile."""

    return ElementRow("stair-north", "monument-stair", "stair",
                      {"from": {"axis_point": {"axis": "W", "along": 0.0}}, "to": {"axis_point": {"axis": "W", "along": 3.0}}, "base": {"level": PN}},
                      {"count": 10, "rise": 0.18, "width": 1.2}, BASIS)


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


class SupportSeatInPlanTests(unittest.TestCase):
    """A support that is nowhere beneath what it carries is not a support, however well the heights agree.

    These run the real producers (the west portico, a real flight) and only move
    the compiled bounds afterwards, so the record, its datums and the declared
    engagement stay exactly what the producers wrote.
    """

    def test_capitals_moved_a_hundred_metres_off_their_columns_are_violated_on_either_plan_axis(self) -> None:
        for axis in (0, 2):                                                             # program x and program z: both are plan
            with self.subTest(axis="xz"[axis // 2]):
                record, bounds, objects, datums = _record_and_bounds()
                bounds = _shifted(bounds, objects["capitals-west"], axis, 100.0)      # heights and the other plan axis untouched
                report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
                self.assertFalse(report.held)
                self.assertTrue(report.fully_checked)
                by_id = {c.relation_id: c for c in report.checks}
                self.assertEqual(by_id["columns-west-support-capitals-west"].status, "violated")
                self.assertEqual(by_id["capitals-west-support-entablature-west"].status, "violated")   # the beam is no longer over its capitals either
                self.assertEqual(by_id["entablature-west-support-pediment-west"].status, "held")        # the pediment moved with nothing: still on its beam
                self.assertEqual(by_id["columns-west-stands-on"].status, "held")                       # a level has no plan extent to leave
                capitals = by_id["columns-west-support-capitals-west"]
                self.assertAlmostEqual(capitals.measured["gap"], 0.0)                                  # the heights still agree ...
                self.assertEqual(capitals.measured["unseated_members"], 6)                             # ... and no capital has a column under it
                self.assertIn("plan", capitals.detail)
                self.assertIn("obj-capitals-west-0", capitals.detail)

    def test_one_capital_between_two_columns_is_not_hidden_by_the_group_extent(self) -> None:
        rows = _rows()
        rows = (rows[0], replace(rows[1], params={**rows[1].params, "half_extent": 0.3}), *rows[2:])   # narrow enough to fit between shafts
        record, bounds, objects, datums = _record_and_bounds(rows)
        centre_x = bounds["obj-capitals-west-2"][0][0] + 0.3
        bounds = _shifted(bounds, ["obj-capitals-west-2"], 0, -centre_x)                # to x = 0, midway between axes 3 and 4, same height
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        by_id = {c.relation_id: c for c in report.checks}
        capitals = by_id["columns-west-support-capitals-west"]
        self.assertEqual(capitals.status, "violated")                                   # the six columns' union covers x = 0; no column does
        self.assertAlmostEqual(capitals.measured["gap"], 0.0)
        self.assertEqual(capitals.measured["unseated_members"], 1)
        self.assertIn("obj-capitals-west-2", capitals.detail)
        self.assertNotIn("obj-capitals-west-1", capitals.detail)
        self.assertEqual([c.relation_id for c in report.checks if c.status == "violated"], ["columns-west-support-capitals-west"])

    def test_one_lifted_capital_is_not_hidden_by_the_lowest(self) -> None:
        record, bounds, objects, datums = _record_and_bounds()
        bounds = _shifted(bounds, ["obj-capitals-west-2"], 1, 0.5)                      # one capital floats half a metre above its column
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        capitals = next(c for c in report.checks if c.relation_id == "columns-west-support-capitals-west")
        self.assertEqual(capitals.status, "violated")
        self.assertAlmostEqual(capitals.measured["object_bottom"], 9.996)               # the group's lowest bottom still meets the columns ...
        self.assertAlmostEqual(capitals.measured["gap"], 0.0)
        self.assertEqual(capitals.measured["unseated_members"], 1)                      # ... and that no longer passes for the lifted one
        self.assertAlmostEqual(capitals.measured["seat_seam_max"], 0.5)
        self.assertIn("+0.5000", capitals.detail)
        beam = next(c for c in report.checks if c.relation_id == "capitals-west-support-entablature-west")
        self.assertEqual(beam.measured["unseated_members"], 0)                          # the beam still has capitals under it ...
        self.assertEqual(beam.status, "violated")                                       # ... but the lifted one now rises through its underside,
        self.assertIn("subject top", beam.detail)                                       # which the group's top face and the datum both say

    def test_a_beam_spans_several_supports_and_may_overhang_them(self) -> None:
        rows = _rows()
        long_overhang = (*rows[:2], replace(rows[2], params={**rows[2].params, "end_overhang": 3.0}), rows[3])
        record, bounds, objects, datums = _record_and_bounds(long_overhang)
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        self.assertTrue(report.held and report.fully_checked, [c.to_dict() for c in report.checks if c.status != "held"])
        beam = next(c for c in report.checks if c.relation_id == "capitals-west-support-entablature-west")
        self.assertEqual((beam.measured["object_members"], beam.measured["unseated_members"]), (1, 0))   # one beam; not asked to fit inside its supports
        two_bays = (*rows[:2], replace(rows[2], references={**rows[2].references, "to": {"grid": ["2", "W"]}}), rows[3])
        record, bounds, objects, datums = _record_and_bounds(two_bays)                # the beam reaches only the first two capitals
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        beam = next(c for c in report.checks if c.relation_id == "capitals-west-support-entablature-west")
        self.assertEqual(beam.status, "held")                                           # a support member carrying nothing is not a failure

    def test_declared_engagement_is_honoured_member_by_member(self) -> None:
        record, bounds, objects, datums = _record_and_bounds(_rows(engagement=0.02))
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        capitals = next(c for c in report.checks if c.relation_id == "columns-west-support-capitals-west")
        self.assertEqual((capitals.status, capitals.measured["unseated_members"]), ("held", 0))
        self.assertAlmostEqual(capitals.measured["seat_seam_max"], 0.0)                 # each capital sits 0.02 into its own column, as declared
        self.assertIn("bounding-box", capitals.detail)                                  # and the held detail claims no more than the boxes show
        self.assertNotIn("contact by construction", capitals.detail)

    def test_a_level_needs_no_plan_extent_and_a_flight_stands_by_its_lowest_step(self) -> None:
        record, bounds, objects, datums = _record_and_bounds()
        bounds = _shifted(bounds, objects["columns-west"], 2, 100.0)                    # the whole array walks 100 m along z
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        by_id = {c.relation_id: c for c in report.checks}
        self.assertEqual(by_id["columns-west-stands-on"].status, "held")                # the piano nobile is everywhere in plan
        self.assertEqual(by_id["columns-west-support-capitals-west"].status, "violated")   # the capitals it left are not
        record, bounds, objects, datums = _record_and_bounds((_stair_row(),))
        report = check_relations(record, bounds=bounds, objects_by_element=objects, datum_values=datums)
        (flight,) = report.checks
        self.assertEqual((flight.relation_id, flight.status), ("stair-north-stands-on", "held"))
        self.assertAlmostEqual(flight.measured["element_bottom"], 3.57)
        self.assertEqual(objects["stair-north"], ["obj-stair-north"])                    # one whole stepped solid, not a box per step
        self.assertAlmostEqual(bounds["obj-stair-north"][0][1], 3.57)                     # it stands on the level by its lowest tread ...
        self.assertAlmostEqual(bounds["obj-stair-north"][1][1], 3.57 + 10 * 0.18)         # ... and rises ten treads above it by construction
        self.assertNotIn("unseated_members", flight.measured)                           # a flight is not pressed onto its level member by member

    def test_level_alignment_honours_and_reports_declared_offsets_in_either_orientation(self) -> None:
        for offset in (-0.02, 0.1):
            row = _stair_row()
            row = replace(row, references={**row.references, "base": {"datum": PN, "offset": offset}})
            record, bounds, objects, datums = _record_and_bounds((row,))
            relation = record.relations[0]
            for reverse in (False, True):
                with self.subTest(offset=offset, reverse=reverse):
                    oriented = replace(relation, subject=relation.object, object=relation.subject) if reverse else relation
                    checked_record = replace(record, relations=(oriented,))
                    check = check_relations(checked_record, bounds=bounds, objects_by_element=objects, datum_values=datums).checks[0]
                    self.assertEqual(check.status, "held")
                    self.assertAlmostEqual(check.measured["gap"], 0.0)
                    self.assertAlmostEqual(check.measured["element_bottom"], check.measured["level_elevation"] + offset)
                    self.assertIn(f"engagement {max(-offset, 0):.4f} m", check.detail)
                    self.assertIn(f"rise {max(offset, 0):.4f} m", check.detail)
                    self.assertIn("datum alignment only", check.detail)
                    moved = _shifted(bounds, objects[row.element_id], 1, 0.01)
                    self.assertEqual(check_relations(checked_record, bounds=moved, objects_by_element=objects, datum_values=datums).checks[0].status,
                                     "violated")


SEAT_ELEMENTS = (
    Entity("piers", "Element@1", {"component_id": "arcade", "producer": "column-array"}),
    Entity("lintel", "Element@1", {"component_id": "arcade", "producer": "beam"}),
)


def _seat(subject_boxes: dict, object_boxes: dict, **parameters) -> tuple:
    """Hand-built boxes: two supports of different heights, one thing carried. Returns (check, report)."""

    relation = Relation("piers-support-lintel", "support", "piers", "lintel", validator=ValidatorBinding("support_contact", tolerance=0.001), parameters=parameters)
    record = StateRecord("arcade", "run-1", SEAT_ELEMENTS, relations=(relation,))
    report = check_relations(record, bounds={**subject_boxes, **object_boxes}, objects_by_element={"piers": list(subject_boxes), "lintel": list(object_boxes)})
    return report.checks[0], report


class SupportSeatCandidateTests(unittest.TestCase):
    """Synthetic boxes for the rule itself: plan overlap and the vertical seam must be met by one and the same subject member."""

    TALL = ([0.0, 0.0, 0.0], [1.0, 10.0, 1.0])       # top at 10, footprint x 0..1
    SHORT = ([5.0, 0.0, 0.0], [6.0, 9.0, 1.0])       # top at 9, footprint x 5..6

    def test_the_seam_on_one_member_and_the_footprint_on_another_is_not_a_seat(self) -> None:
        over_short_at_tall_height = {"obj-lintel": ([5.0, 10.0, 0.0], [6.0, 10.5, 1.0])}
        check, report = _seat({"obj-tall": self.TALL, "obj-short": self.SHORT}, over_short_at_tall_height)
        self.assertEqual(check.status, "violated")
        self.assertAlmostEqual(check.measured["subject_top"], 10.0)                    # the group's highest top meets the lintel bottom ...
        self.assertAlmostEqual(check.measured["gap"], 0.0)
        self.assertEqual(check.measured["unseated_members"], 1)                         # ... but not under it; the pier under it is a metre short
        self.assertIn("obj-short", check.detail)
        self.assertIn("+1.0000", check.detail)
        self.assertFalse(report.held)

    def test_the_same_lintel_over_the_tall_pier_is_seated(self) -> None:
        check, report = _seat({"obj-tall": self.TALL, "obj-short": self.SHORT}, {"obj-lintel": ([0.5, 10.0, 0.0], [3.0, 10.5, 1.0])})
        self.assertEqual((check.status, check.measured["unseated_members"]), ("held", 0))
        self.assertTrue(report.held and report.fully_checked)

    def test_a_footprint_that_only_touches_the_edge_is_still_a_candidate(self) -> None:
        check, _ = _seat({"obj-tall": self.TALL}, {"obj-lintel": ([1.0, 10.0, 0.0], [3.0, 10.5, 1.0])})   # shares the x = 1 edge line
        self.assertEqual(check.status, "held")
        self.assertAlmostEqual(check.measured["seat_plan_gap_max"], 0.0)

    def test_a_declared_rise_or_engagement_moves_each_member_seam(self) -> None:
        raised = {"obj-lintel": ([0.0, 10.1, 0.0], [1.0, 10.5, 1.0])}
        self.assertEqual(_seat({"obj-tall": self.TALL}, raised)[0].status, "violated")             # 0.1 above the pier and nothing declared
        check, _ = _seat({"obj-tall": self.TALL}, raised, rise=0.1)
        self.assertEqual((check.status, check.measured["rise"], check.measured["unseated_members"]), ("held", 0.1, 0))
        embedded = {"obj-lintel": ([0.0, 9.95, 0.0], [1.0, 10.5, 1.0])}
        self.assertEqual(_seat({"obj-tall": self.TALL}, embedded)[0].status, "violated")           # 0.05 into the pier and nothing declared
        check, _ = _seat({"obj-tall": self.TALL}, embedded, engagement_depth=0.05)
        self.assertEqual((check.status, check.measured["unseated_members"]), ("held", 0))

    def test_a_missing_subject_member_bound_is_still_a_typed_failure(self) -> None:
        with self.assertRaises(RelationCheckError):
            relation = Relation("piers-support-lintel", "support", "piers", "lintel", validator=ValidatorBinding("support_contact"))
            check_relations(StateRecord("arcade", "run-1", SEAT_ELEMENTS, relations=(relation,)),
                            bounds={"obj-tall": self.TALL, "obj-lintel": ([0.0, 10.0, 0.0], [1.0, 10.5, 1.0])},
                            objects_by_element={"piers": ["obj-tall", "obj-gone"], "lintel": ["obj-lintel"]})


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
        self.assertEqual(sorted(CHECK_KINDS), ["aperture_exists", "clearance_interval", "lintel_minimum_bearing", "solid_nonpenetration", "support_contact"])
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


class SolidNonpenetrationTests(unittest.TestCase):
    OBJECTS = {"wall-south": ["final-wall"], "window-south": ["final-frame"]}

    def _relation(self, pairs=None):
        return Relation("frame-clear-of-wall", "clearance", "wall-south", "window-south",
                        validator=ValidatorBinding("solid_nonpenetration", tolerance=0),
                        parameters={"object_pairs": pairs or [["final-frame", "final-wall"]]})

    def test_boxes_never_stand_in_for_final_solid_measurements(self):
        report = _report(self._relation(), {"final-frame": WALL, "final-wall": WALL},
                         {"wall-south": ["final-wall"], "window-south": ["final-frame"]})
        self.assertTrue(report.held)
        self.assertFalse(report.fully_checked)
        self.assertEqual(report.checks[0].status, "unchecked")
        self.assertIn("final-frame", report.checks[0].detail)

    def test_the_solid_result_retains_contact_separation_and_positive_volume(self):
        relation = self._relation()
        for classification, distance, volume, status in (("separated", 0.01, 0.0, "held"),
                                                         ("contact", 0.0, 0.0, "held"),
                                                         ("penetrating", 0.0, 1e-12, "violated")):
            with self.subTest(classification=classification):
                report = check_relations(_hand_built(relation), bounds={}, objects_by_element=self.OBJECTS,
                                         solid_measurements={("final-frame", "final-wall"): {
                                             "status": classification, "distance_m": distance, "common_volume_m3": volume}})
                check = report.checks[0]
                self.assertEqual(check.status, status)
                self.assertEqual(check.measured["common_volume_m3_max"], volume)
                self.assertEqual(check.measured["distance_m_min"], distance)
                self.assertIn(classification, check.detail)
                self.assertTrue(report.fully_checked)

    def test_a_known_violation_does_not_hide_an_unmeasured_pair(self):
        relation = self._relation([["final-frame", "final-wall"], ["consumed-jamb", "final-wall"]])
        report = check_relations(_hand_built(relation), bounds={}, objects_by_element=self.OBJECTS, solid_measurements={
            ("final-frame", "final-wall"): {"status": "penetrating", "distance_m": 0.0, "common_volume_m3": 0.2},
            ("consumed-jamb", "final-wall"): {"status": "unchecked", "detail": "not a final delivered object"}})
        self.assertFalse(report.held)
        self.assertFalse(report.fully_checked)
        self.assertEqual(report.checks[0].measured["unchecked_pair_count"], 1)
        self.assertIn("consumed-jamb", report.checks[0].detail)
        self.assertFalse(report.to_dict()["fully_checked"])

    def test_a_real_separated_pair_cannot_check_different_declared_entities(self):
        relation = self._relation()
        values = {("final-frame", "final-wall"): {"status": "separated", "distance_m": 1.0, "common_volume_m3": 0.0}}
        for objects in ({}, {"wall-south": ["final-wall"], "window-south": ["another-frame"], "stair-run": ["final-frame"]}):
            with self.subTest(objects=objects):
                report = check_relations(_hand_built(relation), bounds={}, objects_by_element=objects, solid_measurements=values)
                self.assertEqual(report.checks[0].status, "unchecked")
                self.assertFalse(report.fully_checked)
                self.assertEqual(report.checks[0].measured["checked_pair_count"], 0)
                self.assertIn("window-south", report.checks[0].detail)

    def test_same_host_requires_both_final_objects_to_belong_to_that_host(self):
        relation = Relation("same-host-joint", "clearance", "wall-south", "wall-south",
                            validator=ValidatorBinding("solid_nonpenetration"),
                            parameters={"object_pairs": [["left-jamb", "head"]]})
        values = {("left-jamb", "head"): {"status": "contact", "distance_m": 0.0, "common_volume_m3": 0.0}}
        for objects, expected in (({"wall-south": ["left-jamb", "head"]}, "held"),
                                  ({"wall-south": ["left-jamb"], "window-south": ["head"]}, "unchecked")):
            with self.subTest(objects=objects):
                report = check_relations(_hand_built(relation), bounds={}, objects_by_element=objects, solid_measurements=values)
                self.assertEqual(report.checks[0].status, expected)
                self.assertEqual(report.fully_checked, expected == "held")


class ReportTests(unittest.TestCase):
    def test_a_relation_without_a_validator_is_still_unchecked(self) -> None:
        relation = Relation("stair-passes-wall", "allows_passage", "wall-south", "stair-run")
        check = _report(relation, {}, {}).checks[0]
        self.assertEqual((check.check_kind, check.status, check.detail), ("none", "unchecked", "no validator bound"))


if __name__ == "__main__":
    unittest.main()
