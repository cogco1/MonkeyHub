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

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.operational_state import DependencyEffect, DesignObligation, ObligationStatus
from archflow.project.refs import RunRef
from archflow.state.state_record import (
    CHECK_KINDS,
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

    def test_a_validator_names_a_check_the_spine_can_measure(self) -> None:
        self.assertEqual(sorted(CHECK_KINDS), ["aperture_exists", "clearance_interval", "support_contact"])
        for kind in ("alignment", "meets", "engagement_interval", "separation_interval", "magic"):
            with self.assertRaises(StateRecordError) as raised:                       # a kind nothing measures is refused here, not reported unchecked forever
                ValidatorBinding(kind)
            for registered in CHECK_KINDS:
                self.assertIn(registered, str(raised.exception))

    def test_a_validator_carries_the_field_its_check_measures(self) -> None:
        self.assertEqual(ValidatorBinding("clearance_interval", interval_m=(0.9, 1.5)).interval_m, (0.9, 1.5))
        self.assertEqual(ValidatorBinding("aperture_exists", tolerance=0.01).tolerance, 0.01)
        for wrong in (lambda: ValidatorBinding("clearance_interval", tolerance=0.01),                # an interval check takes no tolerance
                      lambda: ValidatorBinding("clearance_interval"),                                # and needs its interval
                      lambda: ValidatorBinding("clearance_interval", interval_m=(1.5, 0.9)),         # low above high
                      lambda: ValidatorBinding("support_contact", interval_m=(0.0, 1.0)),            # a tolerance check takes no interval
                      lambda: ValidatorBinding("aperture_exists", tolerance=-0.01)):
            with self.assertRaises(StateRecordError):
                wrong()

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

    def test_massing_entities_rebuild_the_spatial_option_exactly(self) -> None:
        from archflow.runtime.project_runner import SchematicPack, bootstrap_developed_state
        from tests.test_project_runner import EVIDENCE, _component
        pack = SchematicPack.from_dict({
            "schema": "SchematicPack@1", "project_id": "demo", "option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico",
            "rationale": "declared from the survey record", "evidence_refs": [EVIDENCE],
            "levels": [{"level_id": "ground", "base_y": 0, "height": 12}], "volumes": [{"volume_id": "block", "min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}],
            "zones": [{"zone_id": "hall", "program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]}], "connections": [],
            "components": [_component("building", None, "whole-building", "one block", ("block",)), _component("portico", "building", "arrival-and-buttress", "front portico")],
            "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"]})
        entities = [Entity(c.component_id, "Component@1", {k: v for k, v in c.to_dict().items() if k not in ("component_id", "parent_component_id")}, parent_id=c.parent_component_id)
                    for c in pack.components]
        entities += [Entity(l["level_id"], "MassingLevel@1", {"base_y": l["base_y"], "height": l["height"]}) for l in pack.levels]
        entities += [Entity(v["volume_id"], "Volume@1", {"min": v["min"], "max": v["max"], "level_ids": v["level_ids"]}) for v in pack.volumes]
        entities += [Entity(z["zone_id"], "Space@1", {"program_node_refs": z["program_node_refs"], "level_ids": z["level_ids"], "volume_ids": z["volume_ids"]}) for z in pack.zones]
        entities += [Entity(c["connection_id"], "Connection@1", {k: v for k, v in c.items() if k != "connection_id"}) for c in pack.connections]
        record = StateRecord(pack.project_id, "run-1", tuple(entities), evidence_refs=pack.evidence_refs,
                             option={"option_id": pack.option_id, "label": pack.label, "typology": pack.typology, "rationale": pack.rationale,
                                     "footprint_cells": [list(c) for c in pack.footprint_cells], "assumption_refs": list(pack.assumption_refs)})
        self.assertEqual(StateRecord.from_dict(record.to_dict()).digest, record.digest)
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / pack.project_id, project_id=pack.project_id, initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            via_record = developed_design_view(record, run=run, branch_id="runner-v1", portfolio_id="declared", selection_decision_ref="decision:declared")
            via_pack = bootstrap_developed_state(pack, run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared")
            self.assertEqual(via_record.state_digest, via_pack.state_digest)                    # one state, whichever door it came through
        with self.assertRaises(StateRecordError):
            StateRecord(pack.project_id, "run-1", tuple(entities), option={"label": "no id"})
        with self.assertRaises(StateRecordError):
            StateRecord(pack.project_id, "run-1", tuple(entities) + (Entity("z2", "Space@1", {"program_node_refs": [], "level_ids": [], "volume_ids": ["nowhere"]}),))

    def test_an_authored_record_is_bound_before_it_can_name_its_state(self) -> None:
        """A portable record has no base; bound_to is the one sanctioned way to give it one."""

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            authored = _record()
            self.assertIsNone(authored.base)
            with self.assertRaises(StateRecordError):
                authored.state_digest                                                   # cannot name its own run
            bound = authored.bound_to(run)
            self.assertEqual(bound.base, run.base)
            self.assertEqual(bound.run_ref, run)
            self.assertEqual(len(bound.state_digest), 64)
            self.assertEqual(authored.digest, StateRecord.from_dict(authored.to_dict()).digest)   # the authored record is untouched
            other = FilesystemProjectRepository.initialize(Path(tmp) / "other", project_id="other", initial_state={"schema": "TestState@1"}).create_run("run-1")
            with self.assertRaises(StateRecordError):
                authored.bound_to(other)                                                # another project's run

    def test_the_compiler_binds_a_program_to_the_record_itself(self) -> None:
        """P102 last step: the record answers the four identity questions, so the compiler takes it directly."""

        from dataclasses import replace
        from archflow.compilers.geometry import compile_geometry_program
        from tests.test_geometry_compiler import COMMITMENT, _proposal, _state

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            record = replace(_record(), run_id=run.run_id, base=run.base)
            self.assertEqual(record.run_ref, run)
            view = developed_design_view(record, run=run)
            self.assertEqual(record.state_digest, view.state_digest)                     # the record cites its own projection
            self.assertEqual(StateRecord.from_dict(record.to_dict()).base, run.base)     # base survives the round trip

            legacy = _state()                                                            # the compiler fixture's own state
            proposal = _proposal(legacy)
            by_view = compile_geometry_program(legacy, proposal, active_commitment_refs=(COMMITMENT,))
            self.assertIsNotNone(by_view.program)
            bound = replace(record, project_id=legacy.project_id, run_id=legacy.run_id, base=legacy.base)
            peer = replace(proposal, project_id=bound.project_id, run_id=bound.run_id, base=bound.base, design_state_digest=bound.state_digest)
            self.assertNotEqual(bound.state_digest, legacy.state_digest)                 # different states, same contract
            by_record = compile_geometry_program(bound, peer, active_commitment_refs=(COMMITMENT,))
            self.assertIsNotNone(by_record.program, [(i.code.value, i.detail) for i in by_record.receipt.issues])
            self.assertEqual([o.object_id for o in by_record.program.objects], [o.object_id for o in by_view.program.objects])

            with self.assertRaises(TypeError):
                compile_geometry_program(object(), peer, active_commitment_refs=(COMMITMENT,))
            stale = replace(peer, design_state_digest="0" * 64)
            self.assertIsNone(compile_geometry_program(bound, stale, active_commitment_refs=(COMMITMENT,)).program)   # not exact-base peers
            with self.assertRaises(StateRecordError):
                _record().state_digest                                                    # a record with no base cannot name its run

    def test_developed_design_view_forwards_to_the_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = developed_design_view(_record(), run=run, option_id="declared", evidence_ref="reading:plan")
            ids = [c.component_id for c in state.selected_schematic.option.proposal.components]
            self.assertEqual(ids, ["building", "portico-columns", "portico-entablature", "portico-west"])
            self.assertEqual(len(state.state_digest), 64)

    def test_binding_changes_state_digest_but_not_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            record = _record()
            bound_a = record.bound_to(RunRef("demo", "run-a", repository.read_head()))
            bound_b = record.bound_to(RunRef("demo", "run-b", repository.read_head()))
            self.assertEqual(bound_a.digest, record.digest)
            self.assertEqual(bound_b.digest, record.digest)
            self.assertNotEqual(bound_a.state_digest, bound_b.state_digest)


if __name__ == "__main__":
    unittest.main()
