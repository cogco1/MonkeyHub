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
import tempfile
import unittest
from pathlib import Path

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.capabilities.discipline_seats import SeatSpec
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity
from archflow.state.stage_workflow import CompositeStageClosureReceipt, StageClosureStatus
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.runtime.project_runner import (
    ProjectRunnerError,
    RunOptions,
    StageExecutionGuard,
    run_project,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.state_record import Entity, StateRecord, StateRecordError, developed_design_view
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import (
    ProjectStage,
    ProjectStageWorkflow,
    StageExitBinding,
    open_stage_run_envelope,
)
from archflow.project.refs import BranchRef

EVIDENCE = "evidence:demo-survey"
BASIS = (EVIDENCE,)


def _component(cid, parent, kind, intent, volumes=()):
    return {"schema": "DesignComponent@1", "component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent,
            "maturity": "schematic", "revision": 1, "volume_ids": list(volumes), "unresolved_child_roles": [], "source_refs": [EVIDENCE]}


def _record(opening_along: float = 6.0, extra_components=(), elements=("portico-columns", "wall-south")) -> StateRecord:
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
        "wall-south": Entity("wall-south", "Element@1", {"component_id": "exterior-walls", "producer": "wall",
                             "references": {"line": {"from": {"grid": ["E", "S"]}, "to": {"grid": ["W", "S"]}, "face": "exterior", "inward": [0, 1]}, "base": {"level": "level-ground"}, "top": {"level": "level-cornice"}},
                             "params": {"thickness": 0.6, "openings": [{"opening_id": "door", "kind": "door", "at": {"host": {"element": "wall-south", "along": opening_along}}, "width": 1.4, "sill": 3.5, "head": 8.0}]}},
                             parent_id="exterior-walls", basis_refs=BASIS),
    }
    entities += [rows[name] for name in elements]
    return StateRecord("demo", "run-1", tuple(entities), evidence_refs=(EVIDENCE,), decision_ref="decision:declared-option",
                       option={"option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico", "rationale": "declared from the survey record",
                               "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"]})


def _seats(phase: DesignPhase):
    return (
        SeatSpec(seat_id="seat-structure", disciplines=(DevelopmentDiscipline.STRUCTURE_SUPPORT,), owned_component_ids=("portico-columns",), phases=(phase,), quadrants=(DeclarationQuadrant.STRUCTURE,)),
        SeatSpec(seat_id="seat-envelope", disciplines=(DevelopmentDiscipline.ENVELOPE_OPENINGS,), owned_component_ids=("exterior-walls",), phases=(phase,), quadrants=(DeclarationQuadrant.OPENINGS,), consumes=("seat-structure",)),
        SeatSpec(seat_id="seat-review", disciplines=(DevelopmentDiscipline.USE,), owned_component_ids=(), phases=(phase,), quadrants=(), reviewer=True),
    )


def _options(**overrides) -> RunOptions:
    fields = dict(commitment_ref="commitment:demo-survey", provider_identity=GeometryProposalProviderIdentity(
        provider_id="runner-test", model_id="scripted", provider_version="1", provider_fingerprint=hashlib.sha256(b"runner-test").hexdigest()))
    fields.update(overrides)
    return RunOptions(**fields)


def _state(record, run, options):
    return developed_design_view(record, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref)


def _stage_guard(repository, run, record, options) -> StageExecutionGuard:
    state = _state(record, run, options)
    workflow = ProjectStageWorkflow(
        project_id=run.project_id,
        workflow_id="runner-test-workflow",
        stages=(
            ProjectStage(
                stage_id="stage-0-test-production",
                stage_index=0,
                phase=DesignPhase.DESIGN_DEVELOPMENT,
                required_roles=("geometry-program", "model-inspection"),
                required_checks=("object-operation-bijection",),
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
        record_kind="project-stage-workflow",
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
        record_kind="stage-run-envelope",
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


class RunTests(unittest.TestCase):
    def _run(self, record: StateRecord, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        repository = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = repository.create_run("run-1")
        options = _options(**overrides)
        state = _state(record, run, options)
        guard = _stage_guard(repository, run, record, options)
        return repository, run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(state.active_phase), options=options)

    def test_two_seats_run_through_the_producer_with_receipts(self) -> None:
        repository, receipt = self._run(_record())
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
        self.assertEqual(receipt["schema"], "RunnerRunReceipt@3")
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

    def test_realized_bounds_of_an_earlier_seat_exclude_a_later_opening(self) -> None:
        with self.assertRaises(ProjectRunnerError) as caught:
            self._run(_record(opening_along=4.75))         # the door would open where a column stands
        self.assertIn("intersects exclusion", str(caught.exception))

    def test_owned_leaf_without_element_fails_typed_unless_relaxed(self) -> None:
        record = _record(elements=("wall-south",))
        with self.assertRaises(ProjectRunnerError):
            self._run(record)
        repository, receipt = self._run(record, strict_coverage=False)
        seats = {s["seat_id"]: s for s in receipt["seat_results"]}
        self.assertEqual(seats["seat-structure"]["status"], "empty")
        self.assertEqual(seats["seat-structure"]["undeclared_components"], ["portico-columns"])
        self.assertEqual(seats["seat-envelope"]["status"], "proposal_accepted")

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
                        ("evidence-coverage",),
                        "close-stage-0-evidence",
                    ),
                    ProjectStage(
                        "stage-1-geometry",
                        1,
                        DesignPhase.DESIGN_DEVELOPMENT,
                        ("geometry-program",),
                        ("object-operation-bijection",),
                        "close-stage-1-geometry",
                    ),
                ),
                basis_refs=("decision:cross-run-test",),
            )
            destination0 = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=predecessor_run.run_id)
            workflow_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind="project-stage-workflow", payload=workflow.to_dict())
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
            predecessor_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind="stage-run-envelope", payload=predecessor.to_dict())
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
            closure_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind="composite-stage-closure", payload=closure.to_dict())
            exit_binding = StageExitBinding.bind(
                predecessor,
                envelope_ref=predecessor_ref.uri,
                closure_ref=closure_ref.uri,
                closure_digest=closure.receipt_digest,
            )
            exit_ref = repository.put_json(run=predecessor_run, destination=destination0, record_kind="stage-exit-binding", payload=exit_binding.to_dict())
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
            successor_ref = repository.put_json(run=run, destination=destination1, record_kind="stage-run-envelope", payload=successor.to_dict())
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


def _ref(uri: str):
    from archflow.project.refs import ProjectRecordRef

    name = uri.rsplit("/", 1)[1]
    return ProjectRecordRef(project_id="demo", relative_path=f"runs/run-1/records/{name}", sha256=name.rsplit("-", 1)[1].split(".json")[0], media_type="application/json")


if __name__ == "__main__":
    unittest.main()
