"""P102: the canonical State Record.

Entities of typed schemas, parameters with lineage, relations with datum
roles / propagation / validator bindings, obligations kept apart; the
record yields kernel dependency edges and a closure; the legacy
DevelopedDesignState is only a forwarded view.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project import FilesystemProjectRepository
from archflow.state.operational_state import DependencyEffect, DesignObligation, ObligationStatus
from archflow.state.state_record import (
    Entity,
    Lineage,
    Parameter,
    Relation,
    StateRecord,
    StateRecordError,
    ValidatorBinding,
    developed_design_view,
)


def _record() -> StateRecord:
    entities = (
        Entity("building", "Component@1", {"semantic_kind": "whole-building", "intent": "villa", "typology": "centralized villa"}),
        Entity("portico-west", "Component@1", {"semantic_kind": "arrival-and-buttress", "intent": "west portico"}, parent_id="building"),
        Entity("portico-columns", "Component@1", {"semantic_kind": "vertical-support", "intent": "six columns"}, parent_id="portico-west"),
        Entity("portico-entablature", "Component@1", {"semantic_kind": "horizontal-load-transfer", "intent": "entablature"}, parent_id="portico-west"),
        Entity("level-piano-nobile", "Level@1", {"role": "piano-nobile", "elevation": 3.57}, basis_refs=("reading:plan",)),
        Entity("axis-1", "GridAxis@1", {"role": "1", "origin": [-10.71, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}),
        Entity("columns-west", "Element@1", {"component_id": "portico-columns", "producer": "column-array", "base_level": "level-piano-nobile"}, lineage=Lineage(introduced_at="stage-2")),
        Entity("entablature-west", "Element@1", {"component_id": "portico-entablature", "producer": "beam", "host": "columns-west"}, lineage=Lineage(introduced_at="stage-2")),
    )
    parameters = (
        Parameter("column_diameter", 0.714, "m", epistemic_status="declared", source_ref="reading:plan"),
        Parameter("column_height", 6.426, "m", expr="9 * column_diameter", inputs=("column_diameter",), source_ref="rule:ionic-nine-diameters"),
    )
    relations = (
        Relation("columns-support-entablature", "support", "columns-west", "entablature-west", datum_role="columns-west-top", propagation="revalidate",
                 validator=ValidatorBinding("support_contact", tolerance=0.001), basis_refs=("reading:plan",)),
    )
    obligations = (DesignObligation(obligation_id="continuous-load-path", statement="a column grid was chosen: complete the load path to the foundation",
                                    source_ref="relation:columns-support-entablature", status=ObligationStatus.OPEN, subject_refs=("entity:columns-west",)),)
    return StateRecord("demo", "run-1", entities, parameters, relations, obligations, evidence_refs=("reading:plan",), decision_ref="decision:declared")


class StateRecordTests(unittest.TestCase):
    def test_record_round_trips_and_digests(self) -> None:
        record = _record()
        self.assertEqual(StateRecord.from_dict(record.to_dict()).digest, record.digest)
        self.assertEqual([e.entity_id for e in record.entities_of("Level@1")], ["level-piano-nobile"])
        self.assertEqual(record.parameter("column_height").inputs, ("column_diameter",))
        self.assertEqual(record.entity("columns-west").lineage.introduced_at, "stage-2")

    def test_relations_and_obligations_stay_apart(self) -> None:
        record = _record()
        relation = record.relations[0]
        self.assertEqual(relation.validator.check_kind, "support_contact")
        self.assertEqual(relation.datum_role, "columns-west-top")
        obligation = record.obligations[0]
        self.assertIs(obligation.status, ObligationStatus.OPEN)
        self.assertEqual(obligation.source_ref, "relation:columns-support-entablature")     # created by the relation, not the relation

    def test_edges_and_closure_follow_references(self) -> None:
        record = _record()
        edges = record.dependency_edges()
        kinds = {(e.upstream_ref, e.downstream_ref, e.effect) for e in edges}
        self.assertIn(("entity:columns-west", "entity:entablature-west", DependencyEffect.REQUIRES_REVALIDATION), kinds)     # the relation
        self.assertIn(("parameter:column_diameter", "parameter:column_height", DependencyEffect.REQUIRES_REVALIDATION), kinds)
        self.assertIn(("entity:level-piano-nobile", "entity:columns-west", DependencyEffect.REQUIRES_REVALIDATION), kinds)   # base_level field
        self.assertIn(("entity:columns-west", "entity:entablature-west", DependencyEffect.INVALIDATES), kinds)                # host field
        closure = record.closure(("entity:level-piano-nobile",))
        self.assertEqual(closure, ("entity:columns-west", "entity:entablature-west", "entity:level-piano-nobile"))
        self.assertEqual(record.closure(("entity:axis-1",)), ("entity:axis-1",))                                             # nothing references the axis yet

    def test_gaps_fail_typed(self) -> None:
        with self.assertRaises(StateRecordError):
            Entity("x", "Widget@1", {})
        with self.assertRaises(StateRecordError):
            Relation("r", "support", "a", "a")
        with self.assertRaises(StateRecordError):
            Relation("r", "sits_on", "a", "b")                                    # not in the kernel vocabulary
        with self.assertRaises(StateRecordError):
            ValidatorBinding("magic")
        record = _record()
        with self.assertRaises(StateRecordError):
            StateRecord("demo", "run-1", record.entities, (Parameter("h", 1.0, "m", inputs=("missing",)),))
        with self.assertRaises(StateRecordError):
            StateRecord("demo", "run-1", record.entities, relations=(Relation("r", "support", "columns-west", "nowhere"),))

    def test_production_entry_accepts_the_record(self) -> None:
        from archflow.capabilities.geometry_proposal import GeometryProposalProductionError, _as_developed_state
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = _as_developed_state(_record(), run=run)
            self.assertEqual(state.selected_schematic.option.option_id, "declared")
            self.assertIs(_as_developed_state(state, run=run), state)                      # legacy input passes through untouched
            bare = StateRecord("demo", "run-1", _record().entities, decision_ref="decision:declared")
            with self.assertRaises(GeometryProposalProductionError):
                _as_developed_state(bare, run=run)                                          # no evidence: typed refusal

    def test_developed_design_view_forwards_to_the_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = developed_design_view(_record(), run=run, option_id="declared", evidence_ref="reading:plan")
            ids = [c.component_id for c in state.selected_schematic.option.proposal.components]
            self.assertEqual(ids, ["building", "portico-columns", "portico-entablature", "portico-west"])
            self.assertEqual(len(state.state_digest), 64)


if __name__ == "__main__":
    unittest.main()
