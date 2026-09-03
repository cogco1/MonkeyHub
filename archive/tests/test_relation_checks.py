"""P102: relations are checked against realized geometry, never healed."""
from __future__ import annotations

import unittest

from archflow.adapters.cad_program import expected_object_bounds
from archflow.capabilities.relation_checks import RelationCheckError, check_relations
from archflow.state.state_record import Entity, Relation, StateRecord, ValidatorBinding
from tests.test_cad_patch import _compile
from tests.test_element_producers import _levels, _produce, _rows


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

    def test_gaps_fail_typed_and_unknown_validators_stay_unchecked(self) -> None:
        record, bounds, objects, datums = _record_and_bounds()
        with self.assertRaises(RelationCheckError):
            check_relations(record, bounds={}, objects_by_element=objects, datum_values=datums)
        other = StateRecord("demo", "run-1", record.entities, (), (Relation("r", "adjacent", "columns-west", "capitals-west", validator=ValidatorBinding("alignment")),
                                                                    Relation("s", "support", "capitals-west", "entablature-west")))
        report = check_relations(other, bounds=bounds, objects_by_element=objects)
        self.assertEqual([c.status for c in report.checks], ["unchecked", "unchecked"])
        self.assertTrue(report.held)                                                     # not violated ...
        self.assertFalse(report.fully_checked)                                           # ... but no green light either
        self.assertFalse(report.to_dict()["fully_checked"])


if __name__ == "__main__":
    unittest.main()
