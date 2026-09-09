"""P089 / P102: the record-driven project runner.

One State Record (components + massing, levels + grid axes, element rows
with references) becomes the real developed-design state through the
retirement-bound view; rows are produced per seat by the canonical
reference-reading producers; handovers carry the realized bounds of
earlier seats as exclusions; relations the producers built are checked
against the compiled bounds; receipts report wall time; gaps fail typed.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.capabilities.discipline_seats import SeatSpec
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity, load_compiled_geometry_program
from archflow.state.stage_workflow import CompositeStageClosureReceipt, StageClosureStatus
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    PROJECT_STAGE_WORKFLOW,
    STAGE_CLOSURE,
    STAGE_EXIT_BINDING,
    STAGE_RUN_ENVELOPE,
)
from archflow.runtime.project_runner import (
    RECORDED_PROPOSAL_IDENTITY,
    Produced,
    ProjectRunnerError,
    RunOptions,
    StageExecutionGuard,
    _check_produced_relations,
    run_project,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.state_record import (
    Entity,
    Parameter,
    Relation,
    StateRecord,
    StateRecordError,
    StateRecordEditKind,
    StateRecordOperator,
    ValidatorBinding,
    apply_state_record_operator,
    developed_design_view,
    project_levels_of,
)
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import (
    ProjectStage,
    ProjectStageWorkflow,
    StageExitBinding,
    open_stage_run_envelope,
)
from archflow.project.inputs import load_authored_record
from archflow.project.refs import BranchRef, record_ref_from_uri
from tools.freeze_project_stage_workflow import freeze_workflow
from tools.open_stage_run import StageRunError, open_stage_run
from tools.run_project import _stage_guard as tool_stage_guard

EVIDENCE = "evidence:demo-survey"
BASIS = (EVIDENCE,)

# What a seat pack declares a live provider would have to present. Nothing in a
# recorded run invokes it, and no record may say it answered. The ids are the
# villa pack's own, because that is exactly the claim the receipts used to make.
DECLARED_LIVE_IDENTITY = GeometryProposalProviderIdentity(
    provider_id="claude-fable-5-1-live-seat", model_id="claude-fable-5-1", provider_version="1",
    provider_fingerprint=hashlib.sha256(b"claude-fable-5-1-live-seat").hexdigest())


def _component(cid, parent, kind, intent, volumes=()):
    return {"schema": "DesignComponent@1", "component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent,
            "maturity": "schematic", "revision": 1, "volume_ids": list(volumes), "unresolved_child_roles": [], "source_refs": [EVIDENCE]}


def _record(opening_along: float = 6.0, extra_components=(), elements=("portico-columns", "wall-south"), extra_entities=(), relations=(), parameters=()) -> StateRecord:
    """The demo block as one State Record: components + massing, three levels, four grid lines, two element rows."""

    components = [
        _component("building", None, "whole-building", "one block", ("block",)),
        _component("main-block", "building", "enclosure-and-load-distribution", "the block"),
        _component("exterior-walls", "main-block", "weather-enclosure-and-opening-host", "walls"),
        _component("portico", "building", "arrival-and-buttress", "front portico"),
        _component("portico-columns", "portico", "vertical-support", "columns"),
        *extra_components,
    ]
    entities = [Entity(c["component_id"], "Component@1", {k: v for k, v in c.items() if k not in ("component_id", "parent_component_id")}, parent_id=c["parent_component_id"]) for c in components]
    entities += [Entity("ground", "MassingLevel@1", {"base_y": 0, "height": 12}), Entity("block", "Volume@1", {"min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}),
                 Entity("hall", "Space@1", {"program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]})]
    entities += [Entity("level-cornice", "Level@1", {"role": "main-cornice", "elevation": 12.0}, basis_refs=BASIS), Entity("level-ground", "Level@1", {"role": "terrain-grade", "elevation": 0.0}, basis_refs=BASIS),
                 Entity("level-piano-nobile", "Level@1", {"role": "piano-nobile", "elevation": 3.5}, basis_refs=BASIS)]
    entities += [Entity("axis-front", "GridAxis@1", {"role": "F", "origin": [0.0, 0.0, -0.2], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),      # the colonnade line
                 Entity("axis-s", "GridAxis@1", {"role": "S", "origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]}, basis_refs=BASIS),           # south face
                 Entity("axis-w", "GridAxis@1", {"role": "W", "origin": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, basis_refs=BASIS),
                 Entity("axis-e", "GridAxis@1", {"role": "E", "origin": [12.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, basis_refs=BASIS)]
    rows = {
        "portico-columns": Entity("columns-front", "Element@1", {"component_id": "portico-columns", "producer": "column-array",
                                  "references": {"at": {"axis_point": {"axis": "F", "along": 6.0}}, "direction": "F", "base": {"level": "level-piano-nobile"}},
                                  "params": {"count": 4, "spacing": 2.5, "radius": 0.4, "height": 6.0}}, parent_id="portico-columns", basis_refs=BASIS),
        "declined-portico-columns": Entity("columns-front-declined", "Element@1", {"component_id": "portico-columns", "producer": "declined",
                                           "params": {"reason": "human decision pending"}}, parent_id="portico-columns", basis_refs=BASIS),
        "wall-south": Entity("wall-south", "Element@1", {"component_id": "exterior-walls", "producer": "wall",
                             "references": {"line": {"from": {"grid": ["E", "S"]}, "to": {"grid": ["W", "S"]}, "face": "exterior", "inward": [0, 1]}, "base": {"level": "level-ground"}, "top": {"level": "level-cornice"}},
                             "params": {"thickness": 0.6, "openings": [{"opening_id": "door", "kind": "door", "at": {"host": {"element": "wall-south", "along": opening_along}}, "width": 1.4, "sill": 3.5, "head": 8.0}]}},
                             parent_id="exterior-walls", basis_refs=BASIS),
    }
    entities += [rows[name] for name in elements]
    entities += list(extra_entities)
    return StateRecord("demo", "run-1", tuple(entities), parameters=tuple(parameters), relations=tuple(relations), evidence_refs=(EVIDENCE,), decision_ref="decision:declared-option",
                       option={"option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico", "rationale": "declared from the survey record",
                               "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"]})


def _seats(phase: DesignPhase):
    return (
        SeatSpec(seat_id="seat-structure", disciplines=(DevelopmentDiscipline.STRUCTURE_SUPPORT,), owned_component_ids=("portico-columns",), phases=(phase,), quadrants=(DeclarationQuadrant.STRUCTURE,)),
        SeatSpec(seat_id="seat-envelope", disciplines=(DevelopmentDiscipline.ENVELOPE_OPENINGS,), owned_component_ids=("exterior-walls",), phases=(phase,), quadrants=(DeclarationQuadrant.OPENINGS,), consumes=("seat-structure",)),
        SeatSpec(seat_id="seat-review", disciplines=(DevelopmentDiscipline.USE,), owned_component_ids=(), phases=(phase,), quadrants=(), reviewer=True),
    )


def _options(**overrides) -> RunOptions:
    fields = dict(commitment_ref="commitment:demo-survey", live_provider_identity=DECLARED_LIVE_IDENTITY)
    fields.update(overrides)
    return RunOptions(**fields)


def _state(record, run, options, phase: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT):
    return developed_design_view(record, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref, phase=phase)


def _stage_guard(repository, run, record, options, required_checks=("support_contact",), *, phase: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT, state=None) -> StageExecutionGuard:
    """A retained one-stage workflow in ``phase`` and its envelope, bound to ``state`` (by default the record projected in that same phase)."""

    state = state if state is not None else _state(record, run, options, phase)
    workflow = ProjectStageWorkflow(
        project_id=run.project_id,
        workflow_id="runner-test-workflow",
        stages=(
            ProjectStage(
                stage_id="stage-0-test-production",
                stage_index=0,
                phase=phase,
                required_roles=("geometry-program", "model-inspection"),
                required_checks=required_checks,
                close_obligation_id="close-stage-0-test-production",
            ),
        ),
        basis_refs=("decision:runner-stage-test",),
    )
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    workflow_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=PROJECT_STAGE_WORKFLOW,
        payload=workflow.to_dict(),
    )
    envelope = open_stage_run_envelope(
        workflow,
        workflow_ref=workflow_ref.uri,
        run_id=run.run_id,
        base_version=run.base.version,
        base_state_sha256=run.base.require_digest(),
        branch_id=options.branch_id,
        branch_epoch=options.branch_epoch,
        subject_ref="state:developed-design-state",
        state_digest=state.state_digest,
        stage_index=0,
        close_obligation=DesignObligation(
            obligation_id="close-stage-0-test-production",
            statement="Remain open until independent stage checks close.",
            source_ref="workflow:runner-test-workflow/stage-0",
            subject_refs=("state:developed-design-state",),
            validator_ref="validator:composite-stage-closure",
        ),
    )
    envelope_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=STAGE_RUN_ENVELOPE,
        payload=envelope.to_dict(),
    )
    return StageExecutionGuard(
        workflow=workflow,
        workflow_record_ref=workflow_ref,
        envelope=envelope,
        envelope_record_ref=envelope_ref,
    )


class BootstrapTests(unittest.TestCase):
    def test_record_becomes_a_real_developed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = developed_design_view(_record(), run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared")
            self.assertEqual(state.active_phase, DesignPhase.DESIGN_DEVELOPMENT)
            self.assertEqual(len(state.state_digest), 64)
            self.assertEqual([c.component_id for c in state.selected_schematic.option.proposal.components][:2], ["building", "exterior-walls"])
            other = repository.create_run("run-2")
            self.assertNotEqual(state.state_digest, developed_design_view(_record(), run=other, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared").selected_schematic.run_id)

    def test_malformed_records_fail_typed(self) -> None:
        with self.assertRaises(StateRecordError):
            StateRecord.from_dict({"schema": "Other@1"})
        with self.assertRaises(StateRecordError):
            StateRecord("demo", "run-1", (Entity("a", "Element@1", {"producer": "prism"}), Entity("a", "Element@1", {"producer": "prism"})))
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "other", project_id="other", initial_state={"schema": "TestState@1"})
            with self.assertRaises(StateRecordError):
                developed_design_view(_record(), run=repository.create_run("run-1"), portfolio_id="declared", branch_id="b", selection_decision_ref="decision:x")


class _RunMixin:
    def _run(self, record: StateRecord, *, required_checks=("support_contact",), phase: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        repository = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = repository.create_run("run-1")
        options = _options(**overrides)
        state = _state(record, run, options, phase)
        guard = _stage_guard(repository, run, record, options, required_checks=required_checks, phase=phase)
        return repository, run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(state.active_phase), options=options)


class RunTests(_RunMixin, unittest.TestCase):
    def test_two_seats_run_through_the_producer_with_receipts(self) -> None:
        repository, receipt = self._run(_record())
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertEqual(receipt["schema"], "RunnerRunReceipt@3")
        self.assertEqual(receipt["coverage_mode"], "strict")
        self.assertEqual(repository.load_json(_ref(receipt["receipt_ref"]))["coverage_mode"], "strict")
        self.assertIn("state_record_ref", receipt)
        self.assertNotIn("accepted", receipt)
        self.assertEqual(receipt["stage"]["status"], "OPEN")
        self.assertFalse(receipt["stage_acceptance_authority"])
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["status"], "proposal_accepted")
        self.assertEqual(seats["seat-structure"]["objects"], 4)
        self.assertEqual(seats["seat-envelope"]["status"], "proposal_accepted")
        self.assertGreaterEqual(seats["seat-envelope"]["objects"], 2)                      # cut wall + aperture
        self.assertEqual(seats["seat-envelope"]["round"], 1)
        self.assertTrue(all(s["wall_time_s"] >= 0.0 for s in receipt["seat_results"]))
        self.assertIn("receipt_ref", receipt)
        program = repository.load_json(_ref(seats["seat-envelope"]["program_ref"]))
        datum_ids = {d["datum_id"] for d in program["interface_datums"]}
        self.assertIn("columns-front-top", datum_ids)                                          # handed over from the structure seat
        self.assertIn("level-cornice", datum_ids)
        record_names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
        self.assertTrue(any(name.startswith("seat-round-receipt-") for name in record_names))
        self.assertFalse(any(name.startswith("runner-stage-receipt-") for name in record_names))
        self.assertFalse(any(name.startswith("runner-element-pack-") or name.startswith("runner-schematic-pack-") for name in record_names))
        checks = repository.load_json(_ref(seats["seat-structure"]["relation_check_ref"]))
        self.assertTrue(checks["held"])                                                        # columns stand on the piano nobile by construction
        self.assertEqual(checks["counts"]["violated"], 0)

    def test_stair_rise_conflicting_with_protected_end_levels_cannot_close(self) -> None:
        # Same endpoint failure as the villa probe; these are diagnostic inputs.
        record = _record(elements=(), extra_components=(
            _component("exterior-stairs", "building", "arrival-and-buttress", "entry stair"),
        ))
        record = replace(record, entities=tuple(
            replace(entity, fields={**entity.fields, "elevation": 3.57})
            if entity.entity_id == "level-piano-nobile" else entity for entity in record.entities
        ) + (Entity("stair-west", "Element@1", {
            "component_id": "exterior-stairs", "producer": "stair",
            "references": {"base": {"level": "level-ground"}, "top": {"level": "level-piano-nobile"},
                           "from": {"axis_point": {"axis": "F", "along": -22.5125}},
                           "to": {"axis_point": {"axis": "F", "along": -14.994}}},
            "params": {"count": 19, "rise": 3.57 / 19, "width": 10.71},
        }, parent_id="exterior-stairs", basis_refs=BASIS),))
        with tempfile.TemporaryDirectory() as temporary:
            repository = FilesystemProjectRepository.initialize(Path(temporary) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            original_head = repository.read_head()
            base_run = repository.create_run("stair-base")
            record = record.bound_to(base_run)
            options = _options(export=False)
            seats = (SeatSpec(seat_id="seat-stair", disciplines=(DevelopmentDiscipline.STRUCTURE_SUPPORT,),
                              owned_component_ids=("exterior-stairs",), phases=(DesignPhase.DESIGN_DEVELOPMENT,),
                              quadrants=(DeclarationQuadrant.STRUCTURE,)),)
            receipt = run_project(repository, run=base_run, stage_guard=_stage_guard(repository, base_run, record, options),
                                  record=record, seats=seats, options=options)
            self.assertEqual(receipt["closure_status"], "SATISFIED")
            successor = apply_state_record_operator(record, StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                protected=("entity:level-ground", "entity:level-piano-nobile"),
                target_ref="entity:stair-west", key="rise", value=3.57 / 19 * 1.1,
            ))
            self.assertEqual(successor.entity("level-ground"), record.entity("level-ground"))
            self.assertEqual(successor.entity("level-piano-nobile"), record.entity("level-piano-nobile"))
            run = repository.create_run("stair-conflict")
            with self.assertRaisesRegex(ProjectRunnerError, "stair-west:.*declared top.*3.57"):
                run_project(repository, run=run, stage_guard=_stage_guard(repository, run, successor, options),
                            record=successor, seats=seats, options=options)
            records = repository.layout.run(run.run_id).records
            self.assertEqual(list(records.glob("stage-closure-*.json")), [])
            self.assertEqual(list(records.glob("stage-exit-binding-*.json")), [])
            self.assertEqual(len(list(records.glob("runner-run-failure-*.json"))), 1)
            self.assertEqual(repository.read_head(), original_head)

    def test_a_completed_stage_closes_and_the_exit_binding_names_that_closure(self) -> None:
        """ADR-007 rule 3: the runner closes the stage from its own checks.

        The stage required ``support_contact``; the producers built support
        relations and every one of them held, so the closure carries no
        finding and an exit binding is derived from it. Both are retained, and
        the binding names the exact closure record and its digest — which is
        all a successor stage is allowed to open against.
        """

        repository, receipt = self._run(_record())

        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(receipt["closure_status"], "SATISFIED")
        self.assertEqual(closure["status"], "SATISFIED")
        self.assertEqual(closure["findings"], [])
        self.assertEqual(closure["profile_id"], "runner-test-workflow")
        self.assertEqual(closure["profile_digest"], receipt["workflow_digest"])
        self.assertEqual(closure["stage_id"], "stage-0-test-production")
        self.assertEqual(closure["subject_digest"], receipt["design_state_digest"])
        self.assertFalse(receipt["workflow_is_harness"])
        measured = {s["relation_check_ref"] for s in receipt["seat_results"] if s["relation_check_ref"]}
        self.assertEqual(len(closure["check_receipt_digests"]), len(measured))
        for ref in measured:
            self.assertIn(_ref(ref).sha256, closure["check_receipt_digests"])

        binding = repository.load_json(_ref(receipt["exit_binding_ref"]))
        self.assertEqual(binding["status"], "SATISFIED")
        self.assertEqual(binding["closure_ref"], receipt["closure_ref"])
        self.assertEqual(binding["closure_digest"], closure["receipt_digest"])
        self.assertEqual(binding["envelope_ref"], receipt["stage_envelope_ref"])
        self.assertEqual(binding["state_digest"], receipt["design_state_digest"])

    def test_a_required_check_nothing_measured_leaves_the_stage_open(self) -> None:
        """The closure is written anyway: it is the statement of why not.

        Nothing in the demo record declares an aperture relation, so the stage
        requiring ``aperture_exists`` has nothing measured for it. The seats
        all ran; the stage still does not close, and no exit binding exists
        for a successor to cite.
        """

        repository, receipt = self._run(_record(), required_checks=("aperture_exists",))

        self.assertTrue(receipt["seat_execution_complete"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(
            closure["findings"],
            [{"code": "missing_check", "requirement_id": "aperture_exists", "receipt_id": None, "refs": []}],
        )
        names = [path.name for path in repository.layout.run("run-1").records.glob("stage-exit-binding-*.json")]
        self.assertEqual(names, [])

    def test_every_record_names_the_provider_that_actually_produced_the_proposal(self) -> None:
        """No run says a model was invoked that was not.

        The round receipt carries the identity the provider presented, and the
        seat pack's declared live identity appears in the run under one key —
        ``declared_live_identity`` — and nowhere else at all.
        """

        repository, receipt = self._run(_record())
        records = repository.layout.run("run-1").records

        rounds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(records.glob("geometry-proposal-round-*.json"))]
        self.assertTrue(rounds)
        for payload in rounds:
            self.assertEqual(payload["model_receipt"]["provider_id"], "runner-recorded-proposal")
            self.assertEqual(payload["model_receipt"]["model_id"], "element-producers")
        expected = {"kind": "recorded", "identity": RECORDED_PROPOSAL_IDENTITY.to_dict(),
                    "declared_live_identity": DECLARED_LIVE_IDENTITY.to_dict()}
        self.assertEqual(receipt["provider"], expected)
        for name in sorted(records.glob("seat-round-receipt-*.json")):
            self.assertEqual(json.loads(name.read_text(encoding="utf-8"))["provider"], expected)
        for path in sorted(records.glob("*.json")):
            elsewhere = json.dumps(_without_declared_live(json.loads(path.read_text(encoding="utf-8"))))
            self.assertNotIn(DECLARED_LIVE_IDENTITY.provider_id, elsewhere, path.name)
            self.assertNotIn(DECLARED_LIVE_IDENTITY.model_id, elsewhere, path.name)

    def test_a_run_with_no_declared_live_provider_still_runs(self) -> None:
        """A seat pack that names no provider is not a broken pack any more."""

        repository, receipt = self._run(_record(), live_provider_identity=None)
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertIsNone(receipt["provider"]["declared_live_identity"])
        self.assertEqual(receipt["provider"]["identity"]["provider_id"], "runner-recorded-proposal")

    def test_realized_bounds_of_an_earlier_seat_exclude_a_later_opening(self) -> None:
        with self.assertRaises(ProjectRunnerError) as caught:
            self._run(_record(opening_along=4.75))         # the door would open where a column stands
        self.assertIn("intersects exclusion", str(caught.exception))

    def test_owned_leaf_without_element_fails_typed_unless_relaxed(self) -> None:
        record = _record(elements=("wall-south",))
        with self.assertRaises(ProjectRunnerError):
            self._run(record)
        repository, receipt = self._run(record, required_checks=(), strict_coverage=False)
        self.assertEqual(receipt["coverage_mode"], "relaxed")
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["status"], "empty")
        self.assertEqual(seats["seat-structure"]["undeclared_components"], ["portico-columns"])
        self.assertEqual(seats["seat-envelope"]["status"], "proposal_accepted")
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(
            closure["findings"],
            [{"code": "seat_incomplete", "requirement_id": "seat-structure:undeclared_components", "receipt_id": None,
              "refs": ["portico-columns"]}],
        )

    def test_declined_component_keeps_stage_open(self) -> None:
        repository, receipt = self._run(_record(elements=("declined-portico-columns", "wall-south")), required_checks=())

        self.assertEqual(receipt["coverage_mode"], "strict")
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["declined_components"], ["portico-columns"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(
            closure["findings"],
            [{"code": "seat_incomplete", "requirement_id": "seat-structure:declined_components", "receipt_id": None,
              "refs": ["portico-columns"]}],
        )

    def test_unretained_or_cross_run_stage_envelope_fails_before_seat_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            other = repository.create_run("run-2")
            options = _options()
            record = _record()
            guard = _stage_guard(repository, other, record, options)
            state = _state(record, run, options)
            with self.assertRaisesRegex(ProjectRunnerError, "another project or run"):
                run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(state.active_phase), options=options)
            record_names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
            self.assertFalse(any(name.startswith("state-record-") for name in record_names))

    def test_cross_run_successor_requires_retained_satisfied_predecessor_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            predecessor_run = repository.create_run("run-0")
            run = repository.create_run("run-1")
            options = _options()
            record = _record()
            state = _state(record, run, options)
            workflow = ProjectStageWorkflow(
                project_id="demo",
                workflow_id="cross-run-test",
                stages=(
                    ProjectStage(
                        "stage-0-evidence",
                        0,
                        DesignPhase.DESIGN_DEVELOPMENT,
                        ("evidence-denominator",),
                        (),
                        "close-stage-0-evidence",
                    ),
                    ProjectStage(
                        "stage-1-geometry",
                        1,
                        DesignPhase.DESIGN_DEVELOPMENT,
                        ("geometry-program",),
                        ("support_contact",),
                        "close-stage-1-geometry",
                    ),
                ),
                basis_refs=("decision:cross-run-test",),
            )
            destination0 = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=predecessor_run.run_id)
            workflow_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind=PROJECT_STAGE_WORKFLOW, payload=workflow.to_dict())
            predecessor = open_stage_run_envelope(
                workflow,
                workflow_ref=workflow_ref.uri,
                run_id=predecessor_run.run_id,
                base_version=predecessor_run.base.version,
                base_state_sha256=predecessor_run.base.require_digest(),
                branch_id=options.branch_id,
                branch_epoch=1,
                subject_ref="state:stage-0-evidence",
                state_digest="0" * 64,
                stage_index=0,
                close_obligation=DesignObligation(
                    "close-stage-0-evidence",
                    "Close only through the evidence checks.",
                    "workflow:cross-run-test/stage-0",
                    subject_refs=("state:stage-0-evidence",),
                    validator_ref="validator:composite-stage-closure",
                ),
            )
            predecessor_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind=STAGE_RUN_ENVELOPE, payload=predecessor.to_dict())
            closure = CompositeStageClosureReceipt(
                profile_id="stage-0-profile",
                profile_digest="1" * 64,
                stage_id=predecessor.stage_id,
                branch=BranchRef(predecessor_run, options.branch_id, 1),
                stage_subject_ref=predecessor.subject_ref,
                subject_digest=predecessor.state_digest,
                check_receipt_digests=(),
                findings=(),
                status=StageClosureStatus.SATISFIED,
            )
            closure_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind=STAGE_CLOSURE, payload=closure.to_dict())
            exit_binding = StageExitBinding.bind(
                predecessor,
                envelope_ref=predecessor_ref.uri,
                closure_ref=closure_ref.uri,
                closure_digest=closure.receipt_digest,
            )
            exit_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind=STAGE_EXIT_BINDING, payload=exit_binding.to_dict())
            successor = open_stage_run_envelope(
                workflow,
                workflow_ref=workflow_ref.uri,
                run_id=run.run_id,
                base_version=run.base.version,
                base_state_sha256=run.base.require_digest(),
                branch_id=options.branch_id,
                branch_epoch=1,
                subject_ref="state:developed-design-state",
                state_digest=state.state_digest,
                stage_index=1,
                close_obligation=DesignObligation(
                    "close-stage-1-geometry",
                    "Close only through geometry checks.",
                    "workflow:cross-run-test/stage-1",
                    subject_refs=("state:developed-design-state",),
                    validator_ref="validator:composite-stage-closure",
                ),
                predecessor=predecessor,
                predecessor_ref=predecessor_ref.uri,
                predecessor_exit=exit_binding,
                predecessor_exit_ref=exit_ref.uri,
            )
            destination1 = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
            successor_ref = repository.put_json(run=run, destination=destination1, record_kind=STAGE_RUN_ENVELOPE, payload=successor.to_dict())
            guard = StageExecutionGuard(
                workflow,
                workflow_ref,
                successor,
                successor_ref,
                predecessor,
                predecessor_ref,
                exit_binding,
                exit_ref,
                closure,
                closure_ref,
            )

            receipt = run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(state.active_phase), options=options)
            self.assertTrue(receipt["seat_execution_complete"])
            self.assertEqual(receipt["stage"]["stage_index"], 1)
            self.assertEqual(receipt["stage"]["status"], "OPEN")


def _podium(component: str = "exterior-walls", x0: float = 10.6) -> Entity:
    """A prism in the envelope seat, standing 0.45 m east of the last column (x 10.15) at the columns' own height."""

    return Entity("podium-east", "Element@1", {"component_id": component, "producer": "prism",
                  "references": {"base": {"level": "level-piano-nobile"}},
                  "params": {"profile": [[x0, -1.0], [x0 + 1.0, -1.0], [x0 + 1.0, 1.0], [x0, 1.0]], "height": 6.0}},
                  parent_id=component, basis_refs=BASIS)


def _lintel(x0: float = 9.35) -> Entity:
    """A prism in the structure seat standing on the columns' published top, over the last column (x 9.35..10.15, top 9.5)."""

    return Entity("lintel-east", "Element@1", {"component_id": "portico-columns", "producer": "prism",
                  "references": {"base": {"datum": "columns-front-top"}},
                  "params": {"profile": [[x0, -1.0], [x0 + 0.8, -1.0], [x0 + 0.8, 1.0], [x0, 1.0]], "height": 0.5}},
                  parent_id="portico-columns", basis_refs=BASIS)


def _columns_carry_lintel(validator=ValidatorBinding("support_contact", tolerance=0.001)) -> Relation:
    """The record's own support of the lintel on the columns, whose datum role names the envelope seat's podium top."""

    return Relation("columns-carry-lintel", "support", "columns-front", "lintel-east", datum_role="podium-east-top", validator=validator)


def _relation_checks(repository, receipt) -> dict[str, dict]:
    """Every retained relation check of the run, by relation id, with the seat that measured it."""

    out: dict[str, dict] = {}
    for seat in receipt["seat_results"]:
        if seat["relation_check_ref"]:
            for check in repository.load_json(_ref(seat["relation_check_ref"]))["checks"]:
                out[check["relation_id"]] = {**check, "seat_id": seat["seat_id"]}
    unmeasured_ref = receipt["relation_checks"]["unmeasured_check_ref"]
    if unmeasured_ref:
        payload = repository.load_json(_ref(unmeasured_ref))
        for check in payload["checks"]:
            out[check["relation_id"]] = {**check, "seat_id": payload["seat_id"], "scope": payload["scope"]}
    return out


class CrossSeatRelationTests(_RunMixin, unittest.TestCase):
    """A relation is measured once every endpoint has extent, in whichever seat that happens; never before, never dropped.

    Every check here is on compiler-predicted bounds (``expected_object_bounds``):
    the receipts say so, and none of these assertions is about a produced solid.
    """

    def test_a_record_support_on_a_later_seat_waits_for_that_seat(self) -> None:
        """The wall's support on the ground is the envelope seat's to measure; the structure seat has no wall."""

        record = _record(relations=(Relation("ground-supports-wall-south", "support", "level-ground", "wall-south",
                                             validator=ValidatorBinding("support_contact", tolerance=0.001)),))
        repository, receipt = self._run(record)                                            # used to raise RelationCheckError in the structure seat
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
        checks = _relation_checks(repository, receipt)
        self.assertEqual(checks["ground-supports-wall-south"]["seat_id"], "seat-envelope")
        self.assertEqual(checks["ground-supports-wall-south"]["status"], "held")
        structure = repository.load_json(_ref({s["seat_id"]: s for s in receipt["seat_results"]}["seat-structure"]["relation_check_ref"]))
        self.assertNotIn("ground-supports-wall-south", {c["relation_id"] for c in structure["checks"]})
        self.assertEqual(structure["basis"], "compiled-predicted-bounds")
        self.assertEqual(receipt["relation_checks"], {"basis": "compiled-predicted-bounds", "unmeasured_check_ref": None, "unmeasured_relation_ids": []})

    def test_a_cross_seat_clearance_is_measured_when_its_second_endpoint_is_produced(self) -> None:
        """Columns (structure seat) to podium (envelope seat): measured in the envelope seat, against both seats' bounds."""

        record = _record(extra_entities=(_podium(),), relations=(
            Relation("columns-clear-of-podium", "clearance", "columns-front", "podium-east", validator=ValidatorBinding("clearance_interval", interval_m=(0.4, 0.5))),))
        repository, receipt = self._run(record, required_checks=("clearance_interval",))
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
        self.assertIsNotNone(receipt["exit_binding_ref"])
        check = _relation_checks(repository, receipt)["columns-clear-of-podium"]
        self.assertEqual((check["seat_id"], check["status"]), ("seat-envelope", "held"))
        self.assertAlmostEqual(check["measured"]["gap"], 0.45, places=6)

    def test_a_cross_seat_clearance_that_does_not_hold_fails_the_closure(self) -> None:
        record = _record(extra_entities=(_podium(),), relations=(
            Relation("columns-clear-of-podium", "clearance", "columns-front", "podium-east", validator=ValidatorBinding("clearance_interval", interval_m=(0.6, 1.0))),))
        repository, receipt = self._run(record, required_checks=("clearance_interval",))
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(closure["findings"], [{"code": "check_failed", "requirement_id": "clearance_interval", "receipt_id": "columns-clear-of-podium", "refs": []}])
        envelope = {s["seat_id"]: s for s in receipt["seat_results"]}["seat-envelope"]
        self.assertEqual(envelope["status"], "proposal_accepted")
        self.assertEqual([i["relation_id"] for i in envelope["issues"] if i.get("code") == "relation_violated"], ["columns-clear-of-podium"])

    def test_an_unmeasured_required_relation_is_not_hidden_by_a_measured_one_of_its_kind(self) -> None:
        """One clearance is measured (hall to court, zones) and held; another of the same kind names an element no seat produces.

        The stage requires ``clearance_interval``. Aggregating by kind would call
        the requirement met on the zone gap alone; the closure must instead name
        the relation nobody measured and stay OPEN.
        """

        record = _record(
            extra_components=(_component("garden-wall", "building", "weather-enclosure-and-opening-host", "an unowned garden wall", ("yard",)),),
            extra_entities=(
                Entity("yard", "Volume@1", {"min": [14, 0, 0], "max": [20, 12, 12], "level_ids": ["ground"]}),
                Entity("court", "Space@1", {"program_node_refs": ["program-node:court"], "level_ids": ["ground"], "volume_ids": ["yard"]}),
                Entity("garden-wall-north", "Element@1", {"component_id": "garden-wall", "producer": "prism", "references": {"base": {"level": "level-ground"}},
                                                          "params": {"profile": [[0, 20], [12, 20], [12, 20.4], [0, 20.4]], "height": 2.0}}, parent_id="garden-wall", basis_refs=BASIS),
            ),
            relations=(
                Relation("hall-to-court-clearance", "clearance", "hall", "court", validator=ValidatorBinding("clearance_interval", interval_m=(1.0, 3.0))),
                Relation("columns-clear-of-garden-wall", "clearance", "columns-front", "garden-wall-north", validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 100.0))),
            ))
        repository, receipt = self._run(record, required_checks=("clearance_interval",))
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertEqual(receipt["unowned_components"], ["garden-wall"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "clearance_interval", "receipt_id": "columns-clear-of-garden-wall", "refs": []}])
        checks = _relation_checks(repository, receipt)
        self.assertEqual(checks["hall-to-court-clearance"]["status"], "held")
        self.assertAlmostEqual(checks["hall-to-court-clearance"]["measured"]["gap"], 2.0)
        unmeasured = checks["columns-clear-of-garden-wall"]
        self.assertEqual((unmeasured["status"], unmeasured["seat_id"], unmeasured["scope"]), ("unchecked", None, "stage-unmeasured"))
        self.assertIn("garden-wall-north", unmeasured["detail"])
        self.assertEqual(receipt["relation_checks"]["unmeasured_relation_ids"], ["columns-clear-of-garden-wall"])
        self.assertIn(_ref(receipt["relation_checks"]["unmeasured_check_ref"]).sha256, closure["check_receipt_digests"])

    def test_a_support_whose_datum_a_later_seat_publishes_waits_for_that_datum_and_holds(self) -> None:
        """Both members are the structure seat's; the datum the check compares against is the envelope seat's podium top.

        Measured in the structure seat the datum comparison would be skipped
        and the relation dropped as held. It waits instead, and is measured
        in the envelope seat with the published value: 9.5, the columns' top.
        """

        record = _record(extra_entities=(_lintel(), _podium()), relations=(_columns_carry_lintel(),))
        repository, receipt = self._run(record)
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
        check = _relation_checks(repository, receipt)["columns-carry-lintel"]
        self.assertEqual((check["seat_id"], check["status"], check["check_kind"]), ("seat-envelope", "held", "support_contact"))
        self.assertAlmostEqual(check["measured"]["datum_value"], 9.5)
        self.assertAlmostEqual(check["measured"]["subject_top"], 9.5)
        structure = repository.load_json(_ref({s["seat_id"]: s for s in receipt["seat_results"]}["seat-structure"]["relation_check_ref"]))
        self.assertNotIn("columns-carry-lintel", {c["relation_id"] for c in structure["checks"]})
        self.assertEqual(receipt["relation_checks"]["unmeasured_relation_ids"], [])

    def test_a_late_datum_that_disagrees_with_the_measured_face_fails_the_check_and_the_closure(self) -> None:
        """The podium is a metre lower, so its top (8.5) does not hold the columns' top (9.5): violated, not silently held."""

        podium = replace(_podium(), fields={**_podium().fields, "params": {**_podium().fields["params"], "height": 5.0}})
        record = _record(extra_entities=(_lintel(), podium), relations=(_columns_carry_lintel(),))
        repository, receipt = self._run(record)                                            # used to close SATISFIED on a check that never compared the datum
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        check = _relation_checks(repository, receipt)["columns-carry-lintel"]
        self.assertEqual((check["seat_id"], check["status"]), ("seat-envelope", "violated"))
        self.assertAlmostEqual(check["measured"]["datum_value"], 8.5)
        self.assertIn("podium-east-top=8.5000 does not hold the measured face 9.5000", check["detail"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(closure["findings"], [{"code": "check_failed", "requirement_id": "support_contact", "receipt_id": "columns-carry-lintel", "refs": []}])
        envelope = {s["seat_id"]: s for s in receipt["seat_results"]}["seat-envelope"]
        self.assertEqual([i["relation_id"] for i in envelope["issues"] if i.get("code") == "relation_violated"], ["columns-carry-lintel"])

    def test_a_datum_no_seat_publishes_leaves_the_check_unmeasured_and_the_stage_open(self) -> None:
        record = _record(extra_entities=(_lintel(),), relations=(_columns_carry_lintel(),))      # no podium: nothing publishes podium-east-top
        repository, receipt = self._run(record)
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        check = _relation_checks(repository, receipt)["columns-carry-lintel"]
        self.assertEqual((check["status"], check["seat_id"], check["scope"]), ("unchecked", None, "stage-unmeasured"))
        self.assertIn("no seat published datum podium-east-top", check["detail"])
        self.assertIn("support_contact", check["detail"])
        self.assertEqual(receipt["relation_checks"]["unmeasured_relation_ids"], ["columns-carry-lintel"])
        closure = repository.load_json(_ref(receipt["closure_ref"]))
        self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "support_contact", "receipt_id": "columns-carry-lintel", "refs": []}])

    def test_an_unbound_relation_waits_for_no_datum_and_is_attributed_to_no_required_checker(self) -> None:
        """No validator: no checker reads the datum role, so the relation is measured as soon as its endpoints exist -
        as ``check_kind`` ``none``, ``unchecked`` - and the required ``support_contact`` neither counts it nor guesses a checker for it."""

        record = _record(extra_entities=(_lintel(), _podium()), relations=(_columns_carry_lintel(validator=None),))
        repository, receipt = self._run(record)
        check = _relation_checks(repository, receipt)["columns-carry-lintel"]
        self.assertEqual((check["seat_id"], check["check_kind"], check["status"], check["detail"]), ("seat-structure", "none", "unchecked", "no validator bound"))
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])      # the producers' bound support relations satisfy the required checker
        self.assertEqual(repository.load_json(_ref(receipt["closure_ref"]))["findings"], [])
        structure = repository.load_json(_ref({s["seat_id"]: s for s in receipt["seat_results"]}["seat-structure"]["relation_check_ref"]))
        self.assertFalse(structure["fully_checked"])                                             # the readout still says something went unchecked

    def test_a_relation_to_a_declined_element_is_reported_unmeasured(self) -> None:
        record = _record(elements=("declined-portico-columns", "wall-south"), relations=(
            Relation("declined-columns-clear-of-wall", "clearance", "columns-front-declined", "wall-south", validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 1.0))),))
        repository, receipt = self._run(record, required_checks=("clearance_interval",))
        self.assertEqual(receipt["closure_status"], "OPEN")
        check = _relation_checks(repository, receipt)["declined-columns-clear-of-wall"]
        self.assertEqual(check["status"], "unchecked")
        self.assertIn("columns-front-declined", check["detail"])
        codes = {(f["code"], f["requirement_id"], f["receipt_id"]) for f in repository.load_json(_ref(receipt["closure_ref"]))["findings"]}
        self.assertIn(("missing_check", "clearance_interval", "declined-columns-clear-of-wall"), codes)
        self.assertIn(("seat_incomplete", "seat-structure:declined_components", None), codes)


class ParameterBindingRunTests(unittest.TestCase):
    """A source parameter edit reaches the geometry through the declared chain and nothing else (B3).

    ``storey`` is declared, ``podium_height = 2 * storey`` is derived, and the
    podium's ``height`` binds ``@podium_height``. The columns' literal height
    is a literal. The successor is saved and read back before it runs, as a
    continued project would be.
    """

    def _record(self):
        podium = replace(_podium(), fields={**_podium().fields, "params": {**_podium().fields["params"], "height": "@podium_height"}})
        return _record(extra_entities=(podium,), parameters=(
            Parameter("storey", 3.0, "m", epistemic_status="declared", source_ref=EVIDENCE),
            Parameter("podium_height", 6.0, "m", expr="2 * storey", inputs=("storey",)),
        ))

    def _bounds(self, repository, receipt, seat_id: str):
        from archflow.adapters.cad_program import expected_object_bounds
        from archflow.capabilities.geometry_proposal import load_compiled_geometry_program

        seat = {s["seat_id"]: s for s in receipt["seat_results"]}[seat_id]
        return expected_object_bounds(load_compiled_geometry_program(repository.load_json(record_ref_from_uri(seat["program_ref"], "demo"))))

    def test_a_source_edit_moves_the_bound_geometry_and_leaves_the_literal_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            options = _options()
            base_run = repository.create_run("run-1")
            record = self._record().bound_to(base_run)
            receipt = run_project(repository, run=base_run, stage_guard=_stage_guard(repository, base_run, record, options), record=record, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=options)
            self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
            before = self._bounds(repository, receipt, "seat-envelope")["obj-podium-east"]
            self.assertAlmostEqual(before["bbox_max"][1] - before["bbox_min"][1], 6.0)
            columns_before = self._bounds(repository, receipt, "seat-structure")["obj-columns-front-0"]

            successor = apply_state_record_operator(record, StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                target_ref="parameter:storey", key="storey", value=3.5))
            self.assertEqual(successor.parameter("podium_height").value, 7.0)
            saved = StateRecord.from_dict(successor.to_dict())                                 # the continued project reads the record back
            run = repository.create_run("run-2")
            saved = saved.bound_to(run)
            receipt = run_project(repository, run=run, stage_guard=_stage_guard(repository, run, saved, options), record=saved, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=options)
            self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
            after = self._bounds(repository, receipt, "seat-envelope")["obj-podium-east"]
            self.assertAlmostEqual(after["bbox_max"][1] - after["bbox_min"][1], 7.0)               # the bound geometry followed the source
            self.assertEqual(self._bounds(repository, receipt, "seat-structure")["obj-columns-front-0"], columns_before)   # the literal did not
            retained = repository.load_json(record_ref_from_uri(receipt["state_record_ref"], "demo"))
            self.assertEqual({p["key"]: p["value"] for p in retained["parameters"]}, {"storey": 3.5, "podium_height": 7.0})
            self.assertEqual({e["entity_id"]: e["fields"]["params"]["height"] for e in retained["entities"] if e["entity_id"] == "podium-east"}, {"podium-east": "@podium_height"})

    def test_a_stale_bound_derived_value_stops_the_run_before_its_first_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            options = _options()
            run = repository.create_run("run-1")
            record = self._record()
            record = replace(record, parameters=(record.parameters[0], replace(record.parameters[1], value=99.0))).bound_to(run)
            with self.assertRaisesRegex(ProjectRunnerError, "podium-east: params.height binds @podium_height: stored value 99.0 of derived parameter podium_height disagrees"):
                run_project(repository, run=run, stage_guard=_stage_guard(repository, run, record, options), record=record, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=options)
            names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
            self.assertFalse(any(name.startswith(("state-record-", "seat-")) for name in names), names)


class StagePhaseTests(_RunMixin, unittest.TestCase):
    """The phase is the stage's (ADR-007, P112): the runner projects the record in the envelope's phase.

    The record states no phase. The envelope does, the projection carries it
    into the state digest, and the guard's equality check now says "this run
    projected the record in the phase its envelope binds" instead of holding
    by construction because the projection was hard-wired to one phase.
    """

    def test_a_schematic_design_stage_runs_to_a_satisfied_closure(self) -> None:
        repository, receipt = self._run(_record(), phase=DesignPhase.SCHEMATIC_DESIGN)
        self.assertEqual(receipt["stage"]["phase"], "schematic_design")
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
        self.assertIsNotNone(receipt["exit_binding_ref"])
        retained = repository.load_json(_ref(receipt["design_state_ref"]))
        self.assertEqual(retained["active_phase"], "schematic_design")
        # binding identity: the same record executed under the development phase is another state
        run = repository.load_run("run-1")
        developed = _state(_record().bound_to(run), run, _options(), DesignPhase.DESIGN_DEVELOPMENT)
        self.assertNotEqual(receipt["design_state_digest"], developed.state_digest)
        self.assertEqual(receipt["design_state_digest"], _state(_record().bound_to(run), run, _options(), DesignPhase.SCHEMATIC_DESIGN).state_digest)

    def test_an_envelope_bound_under_another_phase_is_refused_before_any_write(self) -> None:
        """A schematic stage whose envelope digest was projected in design_development (what a caller
        that ignores the stage phase produces) does not bind the state this run projects."""

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            options, record = _options(), _record()
            guard = _stage_guard(repository, run, record, options, phase=DesignPhase.SCHEMATIC_DESIGN,
                                 state=_state(record.bound_to(run), run, options, DesignPhase.DESIGN_DEVELOPMENT))
            with self.assertRaisesRegex(ProjectRunnerError, "does not bind the exact developed state"):
                run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(DesignPhase.SCHEMATIC_DESIGN), options=options)
            names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
            self.assertFalse(any(name.startswith(("state-record-", "seat-", "stage-closure-")) for name in names), names)

    def test_seats_not_admitted_in_the_envelope_phase_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            options, record = _options(), _record()
            guard = _stage_guard(repository, run, record, options, phase=DesignPhase.SCHEMATIC_DESIGN)
            with self.assertRaisesRegex(ProjectRunnerError, "not admitted in the envelope phase"):
                run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=options)

    def test_a_phase_the_projection_cannot_carry_is_refused_typed(self) -> None:
        """The developed-design projection admits schematic_design and design_development; a later
        phase in the envelope is a typed refusal before the first write, not a manufactured mapping."""

        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            options, record = _options(), _record()
            guard = _stage_guard(repository, run, record, options, phase=DesignPhase.CANDIDATE_COORDINATION,
                                 state=_state(record.bound_to(run), run, options, DesignPhase.DESIGN_DEVELOPMENT))
            with self.assertRaisesRegex(ProjectRunnerError, "cannot carry the envelope phase 'candidate_coordination'"):
                run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(DesignPhase.CANDIDATE_COORDINATION), options=options)
            names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
            self.assertFalse(any(name.startswith(("state-record-", "runner-run-failure-")) for name in names), names)


def _ladder_project(root: Path, phases: tuple[DesignPhase, ...], workflow_id: str = "demo-two-stage") -> tuple[FilesystemProjectRepository, str]:
    """A project holding the demo record as its WIP and a frozen workflow with one stage per phase; answers the workflow ref."""

    repository = FilesystemProjectRepository.initialize(root, project_id="demo", initial_state={"schema": "TestState@1"})
    authored = repository.layout.authored_record
    authored.parent.mkdir(parents=True, exist_ok=True)
    authored.write_text(json.dumps(_record().to_dict()), encoding="utf-8")
    workflow = ProjectStageWorkflow(
        project_id="demo",
        workflow_id=workflow_id,
        stages=tuple(
            ProjectStage(
                stage_id=f"stage-{index}-{'production' if index == 0 else 'coordination'}",
                stage_index=index,
                phase=phase,
                required_roles=("geometry-program",),
                required_checks=() if index == 0 else ("support_contact",),
                close_obligation_id=f"close-stage-{index}",
            )
            for index, phase in enumerate(phases)
        ),
        basis_refs=("decision:demo-two-stage",),
    )
    source = root.parent / f"workflow-{root.name}.json"
    source.write_text(json.dumps(workflow.to_dict()), encoding="utf-8")
    frozen = freeze_workflow(project_root=root, run_id="workflow-001", workflow_path=source, create_run=True)
    return repository, str(frozen["workflow_ref"])


def _run_opened_stage(root: Path, workflow_ref: str, opened: dict) -> dict:
    """Execute a stage ``tools/open_stage_run`` opened, in the phase its retained envelope states."""

    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(str(opened["run_id"]))
    guard = tool_stage_guard(repository, run, workflow_uri=workflow_ref, envelope_uri=str(opened["stage_envelope_ref"]))
    record = load_authored_record(repository).record
    return run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(guard.envelope.phase), options=_options())


class StageLadderRunTests(unittest.TestCase):
    """Stage 0 closes; stage 1 opens against that close and closes too.

    The whole ladder of ADR-007 with nothing hand-built: the workflow is
    frozen by ``tools/freeze_project_stage_workflow.py``, each stage is opened
    by ``tools/open_stage_run.py`` from the project's own work-in-progress
    record, and each run is executed by the runner, which writes the closure
    and derives the exit binding the next stage is allowed to cite.
    """

    PHASES = (DesignPhase.DESIGN_DEVELOPMENT, DesignPhase.DESIGN_DEVELOPMENT)

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "demo"
        self.repository, self.workflow_ref = _ladder_project(self.root, self.PHASES)

    def _run_stage(self, opened: dict) -> dict:
        return _run_opened_stage(self.root, self.workflow_ref, opened)

    def test_stage_zero_closes_and_stage_one_opens_against_its_exit_binding(self) -> None:
        stage0 = open_stage_run(
            project_root=self.root, workflow_uri=self.workflow_ref, stage_index=0, run_id="stage-0-001"
        )
        self.assertEqual(stage0["stage_id"], "stage-0-production")
        self.assertIsNone(stage0["predecessor_exit_binding_ref"])

        first = self._run_stage(stage0)
        self.assertTrue(first["seat_execution_complete"])
        self.assertEqual(first["closure_status"], "SATISFIED")
        self.assertIsNotNone(first["exit_binding_ref"])

        stage1 = open_stage_run(
            project_root=self.root,
            workflow_uri=self.workflow_ref,
            stage_index=1,
            run_id="stage-1-001",
            predecessor_run_id="stage-0-001",
        )
        self.assertEqual(stage1["stage_id"], "stage-1-coordination")
        self.assertEqual(stage1["predecessor_exit_binding_ref"], first["exit_binding_ref"])

        envelope = FilesystemProjectRepository.open(self.root).load_json(
            record_ref_from_uri(str(stage1["stage_envelope_ref"]), "demo")
        )
        self.assertEqual(envelope["stage"]["stage_index"], 1)
        self.assertEqual(envelope["predecessor"]["run_id"], "stage-0-001")
        self.assertEqual(envelope["predecessor"]["exit_binding"]["closure_ref"], first["closure_ref"])

        second = self._run_stage(stage1)
        self.assertTrue(second["seat_execution_complete"])
        self.assertEqual(second["stage"]["stage_index"], 1)
        self.assertEqual(second["closure_status"], "SATISFIED")
        self.assertIsNotNone(second["exit_binding_ref"])

    def test_stage_one_without_a_predecessor_or_without_a_close_is_refused(self) -> None:
        with self.assertRaisesRegex(StageRunError, "--predecessor-run"):
            open_stage_run(
                project_root=self.root, workflow_uri=self.workflow_ref, stage_index=1, run_id="stage-1-001"
            )
        # The refusal happens before the run exists, so nothing is left behind
        # for the next attempt to trip over.
        self.assertFalse(self.repository.layout.run("stage-1-001").manifest.exists())

        with self.assertRaisesRegex(StageRunError, "did not close its stage"):
            open_stage_run(
                project_root=self.root,
                workflow_uri=self.workflow_ref,
                stage_index=1,
                run_id="stage-1-001",
                predecessor_run_id="workflow-001",
            )


class StagePhaseLadderTests(unittest.TestCase):
    """``tools/open_stage_run`` binds each envelope to the record projected in that stage's own phase (P112).

    A schematic stage followed by a development stage: the opener used to
    project every stage in design_development, so a schematic envelope bound a
    digest the runner - projecting in the envelope's phase - could never
    reproduce, and the stage refused to run before its first write.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)

    def test_a_schematic_stage_then_a_development_stage_open_and_close_through_the_real_entrypoint(self) -> None:
        root = self.temporary / "demo"
        repository, workflow_ref = _ladder_project(root, (DesignPhase.SCHEMATIC_DESIGN, DesignPhase.DESIGN_DEVELOPMENT))
        stage0 = open_stage_run(project_root=root, workflow_uri=workflow_ref, stage_index=0, run_id="stage-0-001")
        self.assertEqual(stage0["phase"], "schematic_design")
        run0 = repository.load_run("stage-0-001")
        record = load_authored_record(repository).record
        self.assertEqual(stage0["state_digest"], _state(record, run0, _options(), DesignPhase.SCHEMATIC_DESIGN).state_digest)
        self.assertNotEqual(stage0["state_digest"], _state(record, run0, _options(), DesignPhase.DESIGN_DEVELOPMENT).state_digest)

        first = _run_opened_stage(root, workflow_ref, stage0)                                  # used to raise "does not bind the exact developed state"
        self.assertEqual(first["stage"]["phase"], "schematic_design")
        self.assertEqual(first["design_state_digest"], stage0["state_digest"])
        self.assertEqual(first["closure_status"], "SATISFIED", first["seat_results"])
        self.assertIsNotNone(first["exit_binding_ref"])

        stage1 = open_stage_run(project_root=root, workflow_uri=workflow_ref, stage_index=1, run_id="stage-1-001", predecessor_run_id="stage-0-001")
        self.assertEqual(stage1["phase"], "design_development")
        self.assertEqual(stage1["predecessor_exit_binding_ref"], first["exit_binding_ref"])
        second = _run_opened_stage(root, workflow_ref, stage1)
        self.assertEqual(second["stage"]["phase"], "design_development")
        self.assertEqual(second["design_state_digest"], stage1["state_digest"])
        self.assertEqual(second["closure_status"], "SATISFIED", second["seat_results"])
        # the same record, two binding identities: one per stage phase
        self.assertNotEqual(first["design_state_digest"], second["design_state_digest"])
        self.assertEqual(first["state_record_digest"], second["state_record_digest"])

    def test_a_stage_phase_the_projection_cannot_carry_is_refused_before_the_run_exists(self) -> None:
        root = self.temporary / "demo"
        repository, workflow_ref = _ladder_project(root, (DesignPhase.CANDIDATE_COORDINATION,), workflow_id="demo-coordination")
        with self.assertRaisesRegex(StageRunError, "candidate_coordination.*cannot carry"):
            open_stage_run(project_root=root, workflow_uri=workflow_ref, stage_index=0, run_id="stage-0-001")
        self.assertFalse(repository.layout.run("stage-0-001").manifest.exists())


class ZoneRelationTests(unittest.TestCase):
    """A zone's objects are its volumes, so a zone relation is measurable.

    The villa's fourteen declared relations are all between ``Space@1`` zones —
    corridor to hall clearance, portico voids, stair passages — and a zone
    produces no geometry, so before this every one of them reported
    ``unchecked``. The zone enters the check under its own entity id with the
    declared bounds of the volumes it names.
    """

    def _record(self, interval: tuple[float, float]) -> StateRecord:
        entities = (
            Entity("ground", "MassingLevel@1", {"base_y": 0, "height": 3}),
            Entity("level-ground", "Level@1", {"role": "terrain-grade", "elevation": 0.0}, basis_refs=BASIS),
            Entity("volume-hall", "Volume@1", {"min": [0.0, 0.0, 0.0], "max": [1.0, 3.0, 1.0], "level_ids": ["ground"]}),
            Entity("volume-corridor", "Volume@1", {"min": [1.5, 0.0, 0.0], "max": [2.5, 3.0, 1.0], "level_ids": ["ground"]}),
            Entity("hall", "Space@1", {"program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["volume-hall"]}),
            Entity("corridor", "Space@1", {"program_node_refs": ["program-node:corridor"], "level_ids": ["ground"], "volume_ids": ["volume-corridor"]}),
        )
        relation = Relation(
            "corridor-to-hall-clearance",
            "clearance",
            "corridor",
            "hall",
            validator=ValidatorBinding("clearance_interval", interval_m=interval),
        )
        return StateRecord("demo", "run-1", entities, relations=(relation,), evidence_refs=(EVIDENCE,))

    def _measure(self, interval: tuple[float, float]):
        record = self._record(interval)
        report = _check_produced_relations(record, (), (), Produced((), ()), {}, project_levels_of(record))
        (check,) = report.checks
        return check

    def test_a_zone_gap_inside_the_declared_interval_holds(self) -> None:
        check = self._measure((0.3, 1.0))
        self.assertEqual(check.check_kind, "clearance_interval")
        self.assertEqual(check.status, "held")
        self.assertAlmostEqual(check.measured["gap"], 0.5)

    def test_the_same_gap_outside_the_declared_interval_is_violated(self) -> None:
        check = self._measure((1.0, 2.0))
        self.assertEqual(check.status, "violated")
        self.assertAlmostEqual(check.measured["gap"], 0.5)
        self.assertIn("outside", check.detail)


def _prism_row(component_id: str = "portico-columns", element_id: str = "columns-plinth") -> Entity:
    """One extruded plinth for a component, so a seat has an OCCT-realizable program instead of a column array."""

    return Entity(element_id, "Element@1", {"component_id": component_id, "producer": "prism",
                  "references": {"base": {"level": "level-piano-nobile"}},
                  "params": {"profile": [[0, -1.5], [12, -1.5], [12, -0.5], [0, -0.5]], "height": 0.5}}, parent_id=component_id, basis_refs=BASIS)


def _refuse_rhino(*args, **kwargs):
    raise AssertionError(f"the OCCT export path must never reach Rhino or start a process: {args[:1]}")


def _no_rhino():
    """Fail the test if the run reaches a Rhino entry point or starts any process."""

    import subprocess
    from unittest.mock import patch

    return (patch.multiple("archflow.adapters.cad_execution", prepare_rhino_three_dm_export=_refuse_rhino, execute_rhino_three_dm_export=_refuse_rhino),
            patch.multiple(subprocess, Popen=_refuse_rhino, run=_refuse_rhino))


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _ExportProject:
    """One project, one run and the caller-prepared per-seat workspaces an export writes into."""

    def __init__(self, case: unittest.TestCase, record: StateRecord, **overrides) -> None:
        temporary = tempfile.TemporaryDirectory()
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = FilesystemProjectRepository.initialize(self.root / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        self.run = self.repository.create_run("run-1")
        self.workspace_root = self.root / "workspaces"
        for seat in ("seat-structure", "seat-envelope"):
            (self.workspace_root / f"cad-stage-0-test-production-{seat}").mkdir(parents=True, exist_ok=True)
        self.options = _options(export=True, workspace_root=self.workspace_root, **overrides)
        self.record = record

    def workspace(self, seat_id: str) -> Path:
        return self.workspace_root / f"cad-stage-0-test-production-{seat_id}"

    def files(self, seat_id: str) -> list[str]:
        return sorted(p.name for p in self.workspace(seat_id).iterdir() if p.is_file())

    def run_once(self, record: StateRecord | None = None) -> dict:
        record = record if record is not None else self.record
        guard = _stage_guard(self.repository, self.run, record, self.options)
        return run_project(self.repository, run=self.run, stage_guard=guard, record=record, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=self.options)

    def records(self, prefix: str) -> list[Path]:
        return sorted(self.repository.layout.run("run-1").records.glob(f"{prefix}-*.json"))


try:
    from archflow.adapters import occt_backend as _occt_backend
    _OCCT = _occt_backend.occt_available()
except Exception:  # the backend is optional; the tests below say so
    _OCCT = False
NEEDS_OCCT = unittest.skipUnless(_OCCT, "cadquery-ocp is not installed")


@NEEDS_OCCT
class OcctExportTests(unittest.TestCase):
    """``RunOptions(export=True)`` goes to OCCT: exact STEP and mesh preview per seat, retained and reusable by exact identity."""

    def setUp(self) -> None:
        for patcher in _no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_default_export_writes_step_and_preview_and_retains_the_receipt_bound_to_the_run(self) -> None:
        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)))
        receipt = project.run_once()
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertEqual(receipt["closure_status"], "SATISFIED")
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        for seat_id in ("seat-structure", "seat-envelope"):
            with self.subTest(seat=seat_id):
                cad = seats[seat_id]["cad"]
                self.assertEqual((cad["status"], cad["backend"], cad["path"], cad["readback_verified"]), ("succeeded", "occt", "occt", True))
                self.assertEqual(cad["failures"], [])
                self.assertIn("/records/seat-occt-execution-", cad["execution_ref"])
                self.assertIn("/records/seat-3dm-inspection-", cad["inspection_ref"])
                stem = f"stage-0-test-production-{seat_id}@{seats[seat_id]['program_digest'][:12]}"
                self.assertEqual(project.files(seat_id), [f"{stem}.preview.3dm", f"{stem}.step"])
                self.assertEqual(cad["exact_artifact"]["relative_path"], f"{stem}.step")
                self.assertEqual(cad["preview_artifact"]["relative_path"], f"{stem}.preview.3dm")
                # the retained receipt is the adapter's own, bound to this run, base, branch and program
                retained = project.repository.load_json(record_ref_from_uri(cad["execution_ref"], "demo"))
                self.assertEqual(retained["schema"], "OcctExecutionReceipt@1")
                binding = retained["identity"]["binding"]
                self.assertEqual((binding["project_id"], binding["run_id"], binding["branch_id"], binding["branch_epoch"], binding["stage_id"]),
                                 ("demo", "run-1", "runner-v1", 1, f"stage-0-test-production-{seat_id}"))
                self.assertEqual(binding["base"]["state_sha256"], project.run.base.state_sha256)
                self.assertEqual(binding["program_digest"], seats[seat_id]["program_digest"])
                self.assertEqual(binding["design_state_digest"], receipt["design_state_digest"])
                program = load_compiled_geometry_program(project.repository.load_json(record_ref_from_uri(binding["program_ref"]["uri"], "demo")))
                self.assertEqual(program.program_digest, seats[seat_id]["program_digest"])
                # the files are the bytes the receipt certifies, and the STEP reads back as the seat's objects
                self.assertEqual(_sha256_of(project.workspace(seat_id) / f"{stem}.step"), retained["exact_artifact"]["sha256"])
                self.assertEqual(_sha256_of(project.workspace(seat_id) / f"{stem}.preview.3dm"), retained["preview_artifact"]["sha256"])
                entries = _occt_backend.read_step(project.workspace(seat_id) / f"{stem}.step", length_unit="meter")
                self.assertEqual(sorted(e.name for e in entries), sorted(retained["physical_object_ids"]))
                inspection = project.repository.load_json(record_ref_from_uri(cad["inspection_ref"], "demo"))
                self.assertEqual(inspection["schema"], "ThreeDmInspectionSummary@4")
                self.assertEqual({row["name"] for row in inspection["named_object_bboxes"]}, set(retained["physical_object_ids"]))
        self.assertEqual(len(project.records("seat-occt-execution")), 2)
        self.assertEqual(project.records("seat-rhino-execution"), [])
        structure = project.repository.load_json(record_ref_from_uri(seats["seat-structure"]["cad"]["execution_ref"], "demo"))
        (plinth,) = structure["readback"].values()
        self.assertAlmostEqual(plinth["volume"], 12.0 * 1.0 * 0.5, places=6)

    def test_native_loft_height_fix_rebuilds_an_old_program_cache_then_reuses_the_correct_export(self) -> None:
        from unittest.mock import patch
        from archflow.capabilities import element_producers

        loft = _prism_row()
        profiles = [[[x, height, z] for x, z in ((0.0, -1.5), (1.0, -1.5), (1.0, -0.5), (0.0, -0.5))] for height in (0.4, 1.4)]
        loft = replace(loft, fields={**loft.fields, "producer": "loft", "params": {"profiles": profiles, "profile_size": 4}})
        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(loft,)))
        native_producer = element_producers.PRODUCERS["loft"]

        def prior_producer_without_section_height(row, context):
            produced = native_producer(row, context)
            # The prior native producer passed raw section Y values but neither a seat offset nor its rise relation.
            return replace(produced, operations=tuple(replace(op, parameters=tuple(param for param in op.parameters if param.name != "base_offset"))
                                                       for op in produced.operations),
                           relations=tuple(replace(relation, parameters={}) for relation in produced.relations))

        with patch.dict(element_producers.PRODUCERS, {"loft": prior_producer_without_section_height}):
            before = project.run_once()
        old_seat = next(seat for seat in before["seat_results"] if seat["seat_id"] == "seat-structure")
        old_path = Path(old_seat["cad"]["model"])
        old_sha = _sha256_of(old_path)
        old_body = _occt_backend.measure_shape(next(entry.shape for entry in _occt_backend.read_step(old_path, length_unit="meter") if entry.name == "obj-columns-plinth"))
        self.assertAlmostEqual(old_body.bbox_min[2], 3.5, places=6)
        self.assertEqual(before["closure_status"], "SATISFIED")

        corrected = project.run_once()
        self.assertTrue(corrected["seat_execution_complete"], corrected["seat_results"])
        new_seat = next(seat for seat in corrected["seat_results"] if seat["seat_id"] == "seat-structure")
        self.assertEqual(before["state_record_digest"], corrected["state_record_digest"])
        self.assertEqual(before["design_state_digest"], corrected["design_state_digest"])
        self.assertNotEqual(old_seat["program_digest"], new_seat["program_digest"])
        self.assertNotEqual(old_seat["cad"]["execution_ref"], new_seat["cad"]["execution_ref"])
        self.assertEqual(new_seat["cad"]["path"], "occt")
        new_body = _occt_backend.measure_shape(next(entry.shape for entry in _occt_backend.read_step(Path(new_seat["cad"]["model"]), length_unit="meter") if entry.name == "obj-columns-plinth"))
        self.assertEqual((new_body.valid, new_body.closed, new_body.solid_count), (True, True, 1))
        self.assertAlmostEqual(new_body.bbox_min[2], 3.9, places=6)
        self.assertAlmostEqual(new_body.bbox_max[2], 4.9, places=6)
        self.assertEqual(corrected["closure_status"], "SATISFIED")
        self.assertEqual(_relation_checks(project.repository, corrected)["columns-plinth-stands-on"]["status"], "held")
        self.assertEqual(_sha256_of(old_path), old_sha)

        again = project.run_once()
        reused_seat = next(seat for seat in again["seat_results"] if seat["seat_id"] == "seat-structure")
        self.assertEqual(reused_seat["cad"]["path"], "reused")
        self.assertEqual(reused_seat["cad"]["execution_ref"], new_seat["cad"]["execution_ref"])
        self.assertEqual(again["closure_status"], "SATISFIED")
        self.assertEqual(len(project.files("seat-structure")), 4)

    def test_the_same_program_in_the_same_run_reuses_the_verified_files_and_nothing_else(self) -> None:
        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)))
        first = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        second = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        for seat_id in first:
            with self.subTest(seat=seat_id):
                self.assertEqual(second[seat_id]["path"], "reused")
                self.assertEqual(second[seat_id]["execution_ref"], first[seat_id]["execution_ref"])
                self.assertEqual(second[seat_id]["exact_artifact"], first[seat_id]["exact_artifact"])
                self.assertEqual(second[seat_id]["inspection_ref"], first[seat_id]["inspection_ref"])
        self.assertEqual(len(project.records("seat-occt-execution")), 2)
        self.assertEqual(len(project.files("seat-structure")), 2)

        # a certified file that no longer hashes to its receipt is not reused: a fresh export under a new stem
        stem = first["seat-structure"]["exact_artifact"]["relative_path"]
        (project.workspace("seat-structure") / stem).write_bytes(b"ISO-10303-21; someone edited this")
        third = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        self.assertEqual(third["seat-envelope"]["path"], "reused")
        self.assertEqual(third["seat-structure"]["path"], "occt")
        self.assertNotEqual(third["seat-structure"]["execution_ref"], first["seat-structure"]["execution_ref"])
        self.assertTrue(third["seat-structure"]["exact_artifact"]["relative_path"].endswith(".r2.step"))
        self.assertEqual(len(project.files("seat-structure")), 4)                     # nothing overwritten, nothing deleted
        self.assertEqual(len(project.records("seat-occt-execution")), 3)

        # a changed record is a new design state and a new program: a new export for every seat,
        # never a reuse of the old model (the binding carries the design-state digest, so even the
        # envelope seat, whose own rows did not change, is bound to a different state now)
        taller = replace(project.record, entities=tuple(
            replace(e, fields={**e.fields, "params": {**e.fields["params"], "height": 0.8}}) if e.entity_id == "columns-plinth" else e
            for e in project.record.entities))
        fourth = {s["seat_id"]: s["cad"] for s in project.run_once(taller)["seat_results"]}
        self.assertEqual((fourth["seat-structure"]["path"], fourth["seat-envelope"]["path"]), ("occt", "occt"))
        self.assertNotEqual(fourth["seat-structure"]["exact_artifact"]["sha256"], third["seat-structure"]["exact_artifact"]["sha256"])
        self.assertNotEqual(fourth["seat-structure"]["exact_artifact"]["relative_path"], third["seat-structure"]["exact_artifact"]["relative_path"])

    def test_a_receipt_edited_under_its_own_file_name_is_not_reused_even_though_its_files_still_hash(self) -> None:
        """A prior receipt is read back only as the P036 record its file name claims to be.

        The edit below keeps the whole binding and both artifact digests, so a
        reader that only re-hashed the STEP and the preview would accept it;
        the file name's digest no longer matches the bytes, and that is what
        ``repository.load_json`` refuses. The damaged receipt is left exactly
        where it is; the seat is exported afresh under a new stem and the
        next run reuses that fresh receipt, never the edited one.
        """

        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)))
        first = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        original_ref = record_ref_from_uri(first["seat-structure"]["execution_ref"], "demo")
        receipt_path = project.repository.layout.resolve_record(original_ref)
        edited = json.loads(receipt_path.read_text(encoding="utf-8"))
        edited["readback"] = {oid: {**row, "volume": 0.0} for oid, row in edited["readback"].items()}    # a different claim about the same files
        edited_text = json.dumps(edited)
        receipt_path.write_text(edited_text, encoding="utf-8")
        # what the edit kept: the binding, the certified digests, and files that still hash to them
        self.assertEqual((edited["schema"], edited["status"], edited["readback_verified"], edited["failures"]), ("OcctExecutionReceipt@1", "succeeded", True, []))
        self.assertTrue(first["seat-structure"]["exact_artifact"]["relative_path"].startswith(f"stage-0-test-production-seat-structure@{edited['identity']['binding']['program_digest'][:12]}"))
        for artifact in ("exact_artifact", "preview_artifact"):
            self.assertEqual(_sha256_of(project.workspace("seat-structure") / edited[artifact]["relative_path"]), edited[artifact]["sha256"])
        with self.assertRaises(Exception):
            project.repository.load_json(original_ref)                                                       # P036 itself refuses the edited bytes

        second = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}

        self.assertEqual(second["seat-envelope"]["path"], "reused")
        self.assertEqual(second["seat-structure"]["path"], "occt")
        self.assertEqual((second["seat-structure"]["status"], second["seat-structure"]["readback_verified"], second["seat-structure"]["failures"]), ("succeeded", True, []))
        self.assertNotEqual(second["seat-structure"]["execution_ref"], first["seat-structure"]["execution_ref"])
        self.assertTrue(second["seat-structure"]["exact_artifact"]["relative_path"].endswith(".r2.step"))
        self.assertTrue(second["seat-structure"]["preview_artifact"]["relative_path"].endswith(".r2.preview.3dm"))
        self.assertEqual(len(project.files("seat-structure")), 4)                                           # the certified files stay; nothing overwritten
        self.assertEqual(len(project.records("seat-occt-execution")), 3)
        self.assertEqual(receipt_path.read_text(encoding="utf-8"), edited_text)                            # the damaged evidence is neither repaired nor re-certified
        # the fresh receipt carries the same binding: one identity, a second retained execution of it
        fresh = project.repository.load_json(record_ref_from_uri(second["seat-structure"]["execution_ref"], "demo"))
        self.assertEqual(fresh["identity"]["binding"], edited["identity"]["binding"])

        third = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        self.assertEqual(third["seat-structure"]["path"], "reused")
        self.assertEqual(third["seat-structure"]["execution_ref"], second["seat-structure"]["execution_ref"])
        self.assertEqual(len(project.records("seat-occt-execution")), 3)

    def test_an_exact_only_receipt_of_the_same_binding_is_not_reused_for_an_export_that_needs_the_preview(self) -> None:
        """``execute_occt_export(..., preview=False)`` is a legitimate export whose receipt certifies the STEP alone.

        The runner's export asks for the exact STEP and the mesh preview of the
        same model. A retained exact-only receipt of exactly this binding is
        real, succeeded and intact, and still not what this caller needs: the
        export is made in full under the next stem, the exact-only STEP is
        left untouched, and the complete receipt is what later runs reuse.
        """

        from unittest.mock import patch
        from archflow.adapters import cad_execution

        real = cad_execution.execute_occt_export

        def exact_only(program, *, binding, **kwargs):
            return real(program, binding=binding, **{**kwargs, "preview": False})

        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)))
        with patch.object(cad_execution, "execute_occt_export", side_effect=exact_only):
            first = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        stem = first["seat-structure"]["exact_artifact"]["relative_path"].removesuffix(".step")
        for seat_id in first:
            with self.subTest(seat=seat_id, run="exact-only"):
                self.assertEqual((first[seat_id]["status"], first[seat_id]["readback_verified"], first[seat_id]["path"]), ("succeeded", True, "occt"))
                self.assertIsNone(first[seat_id]["preview_artifact"])
                self.assertNotIn("inspection_ref", first[seat_id])
        self.assertEqual(project.files("seat-structure"), [f"{stem}.step"])
        exact_only_receipt = project.repository.load_json(record_ref_from_uri(first["seat-structure"]["execution_ref"], "demo"))
        self.assertIsNone(exact_only_receipt["preview_artifact"])

        second = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}

        for seat_id in second:
            with self.subTest(seat=seat_id, run="full"):
                cad = second[seat_id]
                self.assertEqual((cad["path"], cad["status"], cad["readback_verified"], cad["failures"]), ("occt", "succeeded", True, []))
                self.assertNotEqual(cad["execution_ref"], first[seat_id]["execution_ref"])
                self.assertTrue(cad["exact_artifact"]["relative_path"].endswith(".r2.step"))
                self.assertTrue(cad["preview_artifact"]["relative_path"].endswith(".r2.preview.3dm"))
                self.assertIn("/records/seat-3dm-inspection-", cad["inspection_ref"])
                retained = project.repository.load_json(record_ref_from_uri(cad["execution_ref"], "demo"))
                prior = project.repository.load_json(record_ref_from_uri(first[seat_id]["execution_ref"], "demo"))
                # the same binding and the same model: no second cache identity was invented for the preview
                self.assertEqual(retained["identity"]["binding"], prior["identity"]["binding"])
                self.assertEqual(retained["physical_object_ids"], prior["physical_object_ids"])
                self.assertEqual({k: v["volume"] for k, v in retained["readback"].items()}, {k: v["volume"] for k, v in prior["readback"].items()})
                self.assertEqual(_sha256_of(project.workspace(seat_id) / cad["preview_artifact"]["relative_path"]), retained["preview_artifact"]["sha256"])
        self.assertEqual(project.files("seat-structure"), [f"{stem}.r2.preview.3dm", f"{stem}.r2.step", f"{stem}.step"])
        self.assertEqual(_sha256_of(project.workspace("seat-structure") / f"{stem}.step"), exact_only_receipt["exact_artifact"]["sha256"])    # untouched
        self.assertEqual(len(project.records("seat-occt-execution")), 4)

        third = {s["seat_id"]: s["cad"] for s in project.run_once()["seat_results"]}
        for seat_id in third:
            with self.subTest(seat=seat_id, run="reuse"):
                self.assertEqual(third[seat_id]["path"], "reused")
                self.assertEqual(third[seat_id]["execution_ref"], second[seat_id]["execution_ref"])
                self.assertEqual(third[seat_id]["preview_artifact"], second[seat_id]["preview_artifact"])
        self.assertEqual(len(project.records("seat-occt-execution")), 4)

    def test_an_operation_occt_does_not_realize_fails_that_seat_by_name_with_no_file_and_no_fallback(self) -> None:
        """The executor's ``CadCapabilityError`` (its own tests raise it for a real array) is a seat-level export failure here.

        Every producer today emits extrusions and lofts, so no record reaches
        that boundary through the runner; the refusal is raised at the
        executor for the structure seat exactly as the adapter raises it, and
        what is tested is what the runner does with it.
        """

        from unittest.mock import patch
        from archflow.adapters import cad_execution
        from archflow.adapters.cad_execution import CadCapabilityError

        real = cad_execution.execute_occt_export

        def refuse_the_structure_seat(program, *, binding, **kwargs):
            if binding.stage_id.endswith("seat-structure"):
                raise CadCapabilityError("OCCT executor cannot realize columns-front (array): block instancing is not realized", op_id="columns-front", kind="array")
            return real(program, binding=binding, **kwargs)

        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)))
        with patch.object(cad_execution, "execute_occt_export", side_effect=refuse_the_structure_seat):
            receipt = project.run_once()
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["status"], "export_failed")
        cad = seats["seat-structure"]["cad"]
        self.assertEqual((cad["status"], cad["backend"], cad["execution_ref"]), ("unsupported", "occt", None))
        self.assertEqual((cad["failures"][0]["op_id"], cad["failures"][0]["kind"]), ("columns-front", "array"))
        self.assertEqual(project.files("seat-structure"), [])
        # the envelope seat (a wall with a cut opening) is unaffected and exported
        self.assertEqual(seats["seat-envelope"]["status"], "proposal_accepted")
        self.assertEqual(seats["seat-envelope"]["cad"]["status"], "succeeded")
        self.assertFalse(receipt["seat_execution_complete"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertEqual(len(project.records("seat-occt-execution")), 1)
        self.assertEqual(project.records("seat-rhino-execution"), [])


@NEEDS_OCCT
class FinalSolidPairRunnerTests(unittest.TestCase):
    """An explicit cross-seat final-solid relation is measured after export and reaches stage closure."""

    RELATION_ID = "plinths-do-not-penetrate"
    PAIR = ("obj-columns-plinth", "obj-envelope-plinth")

    def setUp(self) -> None:
        for patcher in _no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)

    def _pair_record(self, offset_x: float = 12.0, *, pair=None) -> StateRecord:
        structure = _prism_row()
        envelope = _prism_row("exterior-walls", "envelope-plinth")
        envelope = replace(envelope, fields={**envelope.fields, "params": {
            **envelope.fields["params"], "profile": [[x + offset_x, z] for x, z in envelope.fields["params"]["profile"]],
        }})
        return _record(elements=(), extra_entities=(structure, envelope), relations=(
            Relation(self.RELATION_ID, "clearance", structure.entity_id, envelope.entity_id,
                     validator=ValidatorBinding("solid_nonpenetration", tolerance=0.0), parameters={"object_pairs": [list(pair or self.PAIR)]}),
            Relation("legacy-plinth-gap", "clearance", structure.entity_id, envelope.entity_id,
                     validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 2.0))),
        ))

    def _run_required(self, project, *, options=None):
        options = project.options if options is None else options
        guard = _stage_guard(project.repository, project.run, project.record, options, required_checks=("solid_nonpenetration",))
        return run_project(project.repository, run=project.run, stage_guard=guard, record=project.record,
                           seats=_seats(DesignPhase.DESIGN_DEVELOPMENT), options=options)

    def _solid_report(self, project, receipt):
        summary = receipt["relation_checks"]
        self.assertEqual(summary["basis"], "compiled-predicted-bounds")
        self.assertEqual(summary["solid_check_basis"], "occt-step-solid-pairs")
        report_ref = _ref(summary["solid_check_ref"])
        self.assertEqual(report_ref.record_kind, "seat-relation-check")
        report = project.repository.load_json(report_ref)
        self.assertEqual((report["scope"], report["basis"]), ("stage-solid-pairs", "occt-step-solid-pairs"))
        self.assertEqual([check["relation_id"] for check in report["checks"]], [self.RELATION_ID])
        # The earlier bbox reports must not consume this relation or leave a second unchecked copy for closure.
        earlier = _relation_checks(project.repository, receipt)
        self.assertNotIn(self.RELATION_ID, earlier)
        self.assertEqual(earlier["legacy-plinth-gap"]["status"], "held")
        closure = project.repository.load_json(_ref(receipt["closure_ref"]))
        self.assertIn(report_ref.sha256, closure["check_receipt_digests"])
        return report, report["checks"][0], closure

    def test_cross_seat_final_solids_distinguish_separation_contact_and_penetration_in_the_retained_report(self) -> None:
        for offset, classification, volume, expected_status in (
            (13.0, "separated", 0.0, "held"), (12.0, "contact", 0.0, "held"), (11.75, "penetrating", 0.125, "violated"),
        ):
            with self.subTest(classification=classification):
                project = _ExportProject(self, self._pair_record(offset))
                receipt = self._run_required(project)
                report, check, closure = self._solid_report(project, receipt)
                self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
                self.assertEqual(check["status"], expected_status, check)
                self.assertEqual(check["measured"][f"{classification}_pair_count"], 1)
                self.assertAlmostEqual(check["measured"]["common_volume_m3_max"], volume, places=8)
                self.assertAlmostEqual(check["measured"]["distance_m_min"], 1.0 if classification == "separated" else 0.0, places=8)
                self.assertTrue(report["fully_checked"])
                self.assertEqual(set(report["execution_refs"]), {seat["cad"]["execution_ref"] for seat in receipt["seat_results"]})
                self.assertEqual(receipt["closure_status"], "OPEN" if expected_status == "violated" else "SATISFIED")
                self.assertEqual(receipt["exit_binding_ref"] is None, expected_status == "violated")
                self.assertEqual(closure["findings"], ([{"code": "check_failed", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}]
                                                       if expected_status == "violated" else []))

    def test_without_export_or_with_a_missing_final_object_the_required_relation_stays_unchecked(self) -> None:
        for case in ("no-export", "missing-object"):
            with self.subTest(case=case):
                pair = (self.PAIR[0], "obj-not-delivered") if case == "missing-object" else self.PAIR
                project = _ExportProject(self, self._pair_record(pair=pair))
                options = replace(project.options, export=False) if case == "no-export" else project.options
                receipt = self._run_required(project, options=options)
                report, check, closure = self._solid_report(project, receipt)
                self.assertEqual(check["status"], "unchecked", check)
                self.assertFalse(report["fully_checked"])
                self.assertEqual(check["measured"]["unchecked_pair_count"], 1)
                self.assertEqual(receipt["closure_status"], "OPEN")
                self.assertIsNone(receipt["exit_binding_ref"])
                self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}])
                if case == "no-export":
                    self.assertEqual(report["execution_refs"], [])
                    self.assertEqual(project.files("seat-structure") + project.files("seat-envelope"), [])
                else:
                    self.assertIn("obj-not-delivered", check["detail"])

    def test_a_failed_seat_export_does_not_turn_its_predicted_bounds_into_a_solid_check(self) -> None:
        from unittest.mock import patch
        from archflow.adapters import cad_execution

        real = cad_execution.execute_occt_export

        def refuse_structure(program, *, binding, **kwargs):
            if binding.stage_id.endswith("seat-structure"):
                raise cad_execution.CadCapabilityError("the test structure export is unavailable", op_id="columns-plinth", kind="extrude")
            return real(program, binding=binding, **kwargs)

        project = _ExportProject(self, self._pair_record())
        with patch.object(cad_execution, "execute_occt_export", side_effect=refuse_structure):
            receipt = self._run_required(project)
        report, check, closure = self._solid_report(project, receipt)
        self.assertEqual(check["status"], "unchecked", check)
        self.assertFalse(report["fully_checked"])
        seats = {seat["seat_id"]: seat for seat in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["status"], "export_failed")
        self.assertEqual(seats["seat-envelope"]["cad"]["status"], "succeeded")
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        self.assertIn({"code": "missing_check", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}, closure["findings"])

    def test_cached_exports_are_cold_read_again_and_the_solid_result_is_retained_once(self) -> None:
        from unittest.mock import patch
        from archflow.adapters import cad_execution

        project = _ExportProject(self, self._pair_record())
        first = self._run_required(project)
        first_report, _, _ = self._solid_report(project, first)
        with patch.object(cad_execution, "read_step", wraps=cad_execution.read_step) as cold_read:
            second = self._run_required(project)
        report, check, _ = self._solid_report(project, second)
        first_cad = {seat["seat_id"]: seat["cad"] for seat in first["seat_results"]}
        for seat in second["seat_results"]:
            self.assertEqual(seat["cad"]["path"], "reused")
            self.assertEqual(seat["cad"]["execution_ref"], first_cad[seat["seat_id"]]["execution_ref"])
            self.assertEqual(len(project.files(seat["seat_id"])), 2)
        self.assertEqual(cold_read.call_count, 2)
        self.assertEqual({Path(call.args[0]) for call in cold_read.call_args_list}, {Path(cad["model"]) for cad in first_cad.values()})
        self.assertEqual(report["checks"], first_report["checks"])
        self.assertEqual(check["status"], "held")
        self.assertEqual(second["closure_status"], "SATISFIED")
        self.assertEqual(len(project.records("seat-occt-execution")), 2)

    def test_cold_read_failure_or_step_changed_after_export_leaves_the_current_required_check_unchecked(self) -> None:
        from unittest.mock import patch
        from archflow.adapters import cad_execution
        from archflow.runtime import project_runner

        for failure in ("cold-read", "changed-bytes"):
            with self.subTest(failure=failure):
                project = _ExportProject(self, self._pair_record())
                self.assertEqual(self._run_required(project)["closure_status"], "SATISFIED")
                if failure == "cold-read":
                    with patch.object(cad_execution, "read_step", side_effect=cad_execution.OcctBackendError("test final STEP cold read failed")):
                        receipt = self._run_required(project)
                else:
                    real_export = project_runner._export

                    def change_after_export(*args, **kwargs):
                        cad = real_export(*args, **kwargs)
                        if "seat-structure" in Path(cad["model"]).name:
                            Path(cad["model"]).write_bytes(b"ISO-10303-21; changed after its export was returned")
                        return cad

                    with patch.object(project_runner, "_export", side_effect=change_after_export):
                        receipt = self._run_required(project)
                report, check, closure = self._solid_report(project, receipt)
                self.assertEqual(check["status"], "unchecked", check)
                self.assertFalse(report["fully_checked"])
                self.assertEqual(receipt["closure_status"], "OPEN")
                self.assertIsNone(receipt["exit_binding_ref"])
                self.assertTrue(all(seat["cad"]["path"] == "reused" for seat in receipt["seat_results"]))
                self.assertIn("cold read failed" if failure == "cold-read" else "bytes do not match", check["detail"])
                self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}])

    def test_real_separated_objects_cannot_certify_a_relation_to_a_different_entity(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs, read_step

        pair = (self.PAIR[0], "obj-third-plinth")
        record = self._pair_record(13.0, pair=pair)
        third = _prism_row("exterior-walls", "third-plinth")
        third = replace(third, fields={**third.fields, "params": {
            **third.fields["params"], "profile": [[x + 26.0, z] for x, z in third.fields["params"]["profile"]],
        }})
        project = _ExportProject(self, replace(record, entities=record.entities + (third,)))
        receipt = self._run_required(project)
        report, check, closure = self._solid_report(project, receipt)
        entries = [entry for seat in receipt["seat_results"] for entry in read_step(Path(seat["cad"]["model"]), length_unit="meter")]
        self.assertEqual(measure_occt_solid_pairs(entries, object_pairs=(pair,), length_unit="meter")[pair]["status"], "separated")
        self.assertEqual(check["status"], "unchecked", check)
        self.assertIn("does not belong to declared endpoints", check["detail"])
        self.assertIn("envelope-plinth", check["detail"])
        self.assertFalse(report["fully_checked"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}])

    def test_a_same_host_relation_cannot_include_another_hosts_final_object(self) -> None:
        from archflow.adapters.cad_execution import measure_occt_solid_pairs, read_step

        record = self._pair_record(13.0)
        relations = tuple(replace(relation, object=relation.subject) if relation.relation_id == self.RELATION_ID else relation for relation in record.relations)
        project = _ExportProject(self, replace(record, relations=relations))
        receipt = self._run_required(project)
        report, check, closure = self._solid_report(project, receipt)
        entries = [entry for seat in receipt["seat_results"] for entry in read_step(Path(seat["cad"]["model"]), length_unit="meter")]
        self.assertEqual(measure_occt_solid_pairs(entries, object_pairs=(self.PAIR,), length_unit="meter")[self.PAIR]["status"], "separated")
        self.assertEqual(check["status"], "unchecked", check)
        self.assertIn("does not belong to declared endpoints", check["detail"])
        self.assertFalse(report["fully_checked"])
        self.assertEqual(receipt["closure_status"], "OPEN")
        self.assertIsNone(receipt["exit_binding_ref"])
        self.assertEqual(closure["findings"], [{"code": "missing_check", "requirement_id": "solid_nonpenetration", "receipt_id": self.RELATION_ID, "refs": []}])

    def test_two_real_outputs_of_the_same_column_array_can_satisfy_its_same_host_relation(self) -> None:
        pair = ("obj-columns-front-0", "obj-columns-front-1")
        envelope = _prism_row("exterior-walls", "envelope-plinth")
        envelope = replace(envelope, fields={**envelope.fields, "params": {
            **envelope.fields["params"], "profile": [[x, z - 1.0] for x, z in envelope.fields["params"]["profile"]],
        }})
        record = _record(elements=("portico-columns",), extra_entities=(envelope,), relations=(
            Relation(self.RELATION_ID, "clearance", "columns-front", "columns-front",
                     validator=ValidatorBinding("solid_nonpenetration", tolerance=0.0), parameters={"object_pairs": [list(pair)]}),
            Relation("legacy-plinth-gap", "clearance", "columns-front", "envelope-plinth",
                     validator=ValidatorBinding("clearance_interval", interval_m=(0.0, 2.0))),
        ))
        project = _ExportProject(self, record)
        receipt = self._run_required(project)
        report, check, closure = self._solid_report(project, receipt)
        structure = next(seat for seat in receipt["seat_results"] if seat["seat_id"] == "seat-structure")
        retained = project.repository.load_json(_ref(structure["cad"]["execution_ref"]))
        self.assertTrue(set(pair).issubset(retained["physical_object_ids"]))
        self.assertEqual(check["status"], "held", check)
        self.assertEqual(check["measured"]["separated_pair_count"], 1)
        self.assertAlmostEqual(check["measured"]["distance_m_min"], 1.7, places=8)
        self.assertAlmostEqual(check["measured"]["common_volume_m3_max"], 0.0, places=8)
        self.assertTrue(report["fully_checked"])
        self.assertEqual(report["execution_refs"], [structure["cad"]["execution_ref"]])
        self.assertEqual(receipt["closure_status"], "SATISFIED")
        self.assertIsNotNone(receipt["exit_binding_ref"])
        self.assertEqual(closure["findings"], [])

    def test_reversing_a_cross_seat_pair_preserves_its_endpoint_binding(self) -> None:
        project = _ExportProject(self, self._pair_record(13.0, pair=tuple(reversed(self.PAIR))))
        receipt = self._run_required(project)
        report, check, closure = self._solid_report(project, receipt)
        self.assertEqual(check["status"], "held", check)
        self.assertEqual(check["measured"]["separated_pair_count"], 1)
        self.assertAlmostEqual(check["measured"]["distance_m_min"], 1.0, places=8)
        self.assertTrue(report["fully_checked"])
        self.assertEqual(receipt["closure_status"], "SATISFIED")
        self.assertIsNotNone(receipt["exit_binding_ref"])
        self.assertEqual(closure["findings"], [])


class CadBackendSelectionTests(unittest.TestCase):
    def test_run_options_refuse_a_backend_nobody_implements(self) -> None:
        with self.assertRaisesRegex(ProjectRunnerError, "cad_backend"):
            _options(cad_backend="freecad")
        self.assertEqual(_options().cad_backend, "occt")

    def test_the_patch_oracle_is_refused_by_name_under_occt_and_only_taken_with_rhino(self) -> None:
        """OCCT never patches: an oracle asked of it is refused naming the backend that has one, never ignored or run through Rhino unasked."""

        with self.assertRaisesRegex(ProjectRunnerError, r"patch_oracle.*--cad-backend rhino"):
            _options(export=True, patch_oracle=True)
        with self.assertRaisesRegex(ProjectRunnerError, r"patch_oracle.*--cad-backend rhino"):
            _options(export=True, cad_backend="occt", patch_oracle=True)
        self.assertTrue(_options(export=True, cad_backend="rhino", patch_oracle=True).patch_oracle)
        # the default PowerShell path is Rhino's launcher carried unread; it is not an error under OCCT
        self.assertEqual(_options(export=True, powershell=Path("powershell.exe")).cad_backend, "occt")

    def test_rhino_is_taken_only_when_named_and_occt_is_not_touched(self) -> None:
        from unittest.mock import patch

        class RhinoReached(Exception):
            pass

        def reached(*args, **kwargs):
            raise RhinoReached("prepare_rhino_three_dm_export was called")

        project = _ExportProject(self, _record(elements=("wall-south",), extra_entities=(_prism_row(),)), cad_backend="rhino")
        with patch("archflow.adapters.cad_execution.prepare_rhino_three_dm_export", side_effect=reached), \
             patch("archflow.adapters.cad_execution.execute_occt_export", side_effect=AssertionError("OCCT must not run for the rhino backend")):
            with self.assertRaises(RhinoReached):
                project.run_once()
        self.assertEqual(project.files("seat-structure"), [])
        self.assertEqual(len(project.records("runner-run-failure")), 1)
        self.assertEqual(project.records("seat-occt-execution"), [])


class RunProjectCliTests(unittest.TestCase):
    """``tools/run_project.py --export`` goes to OCCT unless ``--cad-backend rhino`` is named."""

    def _opened(self) -> tuple[Path, str, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "demo"
        repository, workflow_ref = _ladder_project(root, (DesignPhase.DESIGN_DEVELOPMENT,))
        # the ladder project's WIP record has a column array; give the structure seat a prism the OCCT executor realizes
        repository.layout.authored_record.write_text(json.dumps(_record(elements=("wall-south",), extra_entities=(_prism_row(),)).to_dict()), encoding="utf-8")
        seats = repository.layout.authored_record.parent / "seats.json"
        seats.write_text(json.dumps({
            "schema": "RunnerSeats@1", "commitment_ref": "commitment:demo-survey",
            "seats": [
                {"seat_id": "seat-structure", "disciplines": ["structure_support"], "owned_component_ids": ["portico-columns"], "phases": ["design_development"], "quadrants": ["structure"]},
                {"seat_id": "seat-envelope", "disciplines": ["envelope_openings"], "owned_component_ids": ["exterior-walls"], "phases": ["design_development"], "quadrants": ["openings"], "consumes": ["seat-structure"]},
            ],
        }), encoding="utf-8")
        opened = open_stage_run(project_root=root, workflow_uri=workflow_ref, stage_index=0, run_id="stage-0-001")
        return root, workflow_ref, opened

    def _argv(self, root: Path, workflow_ref: str, opened: dict, *extra: str) -> list[str]:
        return ["--project", str(root), "--run", "stage-0-001", "--workflow-ref", workflow_ref,
                "--stage-envelope-ref", str(opened["stage_envelope_ref"]), "--export", *extra]

    @NEEDS_OCCT
    def test_export_defaults_to_occt_and_leaves_step_and_preview_in_the_run_workspaces(self) -> None:
        from unittest.mock import patch
        from tools.run_project import main

        root, workflow_ref, opened = self._opened()
        patchers = _no_rhino()
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(main(self._argv(root, workflow_ref, opened)), 0)
        repository = FilesystemProjectRepository.open(root)
        workspaces = repository.layout.run("stage-0-001").workspaces
        names = sorted(p.name for p in workspaces.rglob("*") if p.is_file())
        self.assertEqual(len(names), 4, names)
        self.assertTrue(any(n.endswith(".step") for n in names) and any(n.endswith(".preview.3dm") for n in names))
        records = [p.name for p in repository.layout.run("stage-0-001").records.glob("*.json")]
        self.assertEqual(sum(n.startswith("seat-occt-execution-") for n in records), 2)
        self.assertFalse(any(n.startswith("seat-rhino-execution-") for n in records))

    def test_the_rhino_backend_is_an_explicit_choice(self) -> None:
        from unittest.mock import patch
        import tools.run_project as cli

        root, workflow_ref, opened = self._opened()
        seen = []

        def capture(repository, *, run, stage_guard, record, seats, options):
            seen.append(options)
            return {"seat_results": [], "unowned_components": [], "seat_execution_complete": True, "stage": {"status": "OPEN"}, "wall_time_s": 0.0,
                    "receipt_ref": "project://demo/x", "closure_status": "OPEN", "closure_ref": "project://demo/y", "exit_binding_ref": None}

        with patch.object(cli, "run_project", side_effect=capture):
            cli.main(self._argv(root, workflow_ref, opened, "--cad-backend", "rhino"))
            cli.main(self._argv(root, workflow_ref, opened))
        self.assertEqual([o.cad_backend for o in seen], ["rhino", "occt"])
        self.assertTrue(all(o.export for o in seen))
        with self.assertRaises(SystemExit):
            cli.main(self._argv(root, workflow_ref, opened, "--cad-backend", "freecad"))


class PriorExportTests(unittest.TestCase):
    def test_latest_succeeded_export_of_the_stage_with_a_present_model_is_the_patch_base(self) -> None:
        import json
        import time
        from archflow.runtime.project_runner import _prior_export

        with tempfile.TemporaryDirectory() as tmp:
            records, workspace = Path(tmp) / "records", Path(tmp) / "cad-stage"
            records.mkdir(); workspace.mkdir()

            def receipt(name, *, stage, digest, artifact, status="succeeded", present=True):
                payload = {"status": status, "artifact_relative_path": artifact, "identity": {"binding": {"stage_id": stage, "program_digest": digest, "program_ref": {"uri": f"project://demo/runs/run-1/branches/b/records/{stage}-geometry-program-{'0' * 64}.json"}}}}
                (records / f"seat-rhino-execution-{name}.json").write_text(json.dumps(payload), encoding="utf-8")
                if present:
                    (workspace / artifact).write_bytes(b"3dm")
                time.sleep(0.01)

            receipt("a" * 64, stage="stage-x", digest="d1", artifact="stage-x@d1.3dm")
            receipt("b" * 64, stage="stage-y", digest="d9", artifact="stage-y@d9.3dm")                       # another stage
            receipt("c" * 64, stage="stage-x", digest="d2", artifact="stage-x@d2.3dm", status="failed")      # failed: skipped
            receipt("d" * 64, stage="stage-x", digest="d3", artifact="stage-x@d3.3dm", present=False)         # model gone: skipped
            found = _prior_export(records, workspace, "stage-x", "d4")
            self.assertIsNotNone(found)
            payload, model = found
            self.assertEqual(payload["identity"]["binding"]["program_digest"], "d1")
            self.assertEqual(model.name, "stage-x@d1.3dm")
            self.assertNotIn("_reused_path", payload)
            same = _prior_export(records, workspace, "stage-x", "d1")
            self.assertIn("_reused_path", same[0])                                                        # identical program: reuse
            self.assertIsNone(_prior_export(records, workspace, "stage-z", "d1"))


def _without_declared_live(value):
    """The payload with every ``declared_live_identity`` block removed."""

    if isinstance(value, dict):
        return {k: _without_declared_live(v) for k, v in value.items() if k != "declared_live_identity"}
    if isinstance(value, list):
        return [_without_declared_live(item) for item in value]
    return value


def _ref(uri: str):
    from archflow.project.refs import ProjectRecordRef

    name = uri.rsplit("/", 1)[1]
    return ProjectRecordRef(project_id="demo", relative_path=f"runs/run-1/records/{name}", sha256=name.rsplit("-", 1)[1].split(".json")[0], media_type="application/json")


if __name__ == "__main__":
    unittest.main()
