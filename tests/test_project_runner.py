"""P089 (first cut): the record-driven project runner.

A schematic pack becomes a real developed-design state; an element pack
is produced per seat through the real producer; handovers carry the
realized bounds of earlier seats as exclusions; receipts report wall
time; gaps fail typed.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from archflow.capabilities.declaration import DeclarationQuadrant
from archflow.capabilities.discipline_seats import SeatSpec
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.runtime.project_runner import (
    ElementPack,
    ProjectRunnerError,
    RunOptions,
    SchematicPack,
    StageExecutionGuard,
    bootstrap_developed_state,
    run_project,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.geometry_program import ProjectLevel, ProjectLevels
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


def _schematic(extra_components=()) -> SchematicPack:
    from archflow.state.spatial import DesignComponent

    probe = DesignComponent.from_dict(_component("building", None, "whole-building", "one block", ("block",)))
    fields = probe.to_dict()
    def comp(cid, parent, kind, intent, volumes=()):
        payload = dict(fields); payload.update({"component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent, "volume_ids": list(volumes)})
        return payload
    components = [
        comp("building", None, "whole-building", "one block", ("block",)),
        comp("main-block", "building", "enclosure-and-load-distribution", "the block"),
        comp("exterior-walls", "main-block", "weather-enclosure-and-opening-host", "walls"),
        comp("portico", "building", "arrival-and-buttress", "front portico"),
        comp("portico-columns", "portico", "vertical-support", "columns"),
        *extra_components,
    ]
    return SchematicPack.from_dict({
        "schema": "SchematicPack@1", "project_id": "demo", "option_id": "declared-option", "label": "demo declared schematic", "typology": "test block with a portico",
        "rationale": "declared from the survey record", "evidence_refs": [EVIDENCE],
        "levels": [{"level_id": "ground", "base_y": 0, "height": 12}], "volumes": [{"volume_id": "block", "min": [0, 0, 0], "max": [12, 12, 12], "level_ids": ["ground"]}],
        "zones": [{"zone_id": "hall", "program_node_refs": ["program-node:hall"], "level_ids": ["ground"], "volume_ids": ["block"]}],
        "connections": [], "components": components, "footprint_cells": [[0, 0], [1, 0], [0, 1], [1, 1]], "assumption_refs": ["assumption:declared-schematic"],
    })


def _levels() -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-cornice", "main-cornice", 12.0, BASIS), ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel("level-piano-nobile", "piano-nobile", 3.5, BASIS)))


def _elements(opening_along=6.0) -> ElementPack:
    return ElementPack.from_dict({"schema": "ElementPack@1", "elements": [
        {"element_id": "portico-columns", "component_id": "portico-columns", "producer": "column-array", "base_level": "level-piano-nobile",
         "params": {"origin": [6.0, -0.2], "direction": [1, 0], "count": 4, "spacing": 2.5, "radius": 0.4, "height": 6.0, "basis_refs": [EVIDENCE]}},
        {"element_id": "wall-south", "component_id": "exterior-walls", "producer": "wall", "base_level": "level-ground",
         "params": {"origin": [12.0, 0.0], "direction": [-1, 0], "length": 12.0, "thickness": 0.6, "top_level": "level-cornice",
                    "openings": [{"opening_id": "door", "kind": "door", "along": opening_along, "width": 1.4, "sill": 3.5, "head": 8.0}]}},
    ]})


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


def _stage_guard(repository, run, pack, options) -> StageExecutionGuard:
    state = bootstrap_developed_state(
        pack,
        run=run,
        portfolio_id=options.portfolio_id,
        branch_id=options.branch_id,
        selection_decision_ref=options.selection_decision_ref,
    )
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
    def test_pack_becomes_a_real_developed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            run = repository.create_run("run-1")
            state = bootstrap_developed_state(_schematic(), run=run, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared")
            self.assertEqual(state.active_phase, DesignPhase.DESIGN_DEVELOPMENT)
            self.assertEqual(len(state.state_digest), 64)
            self.assertEqual([c.component_id for c in state.selected_schematic.option.proposal.components][:2], ["building", "exterior-walls"])
            other = repository.create_run("run-2")
            self.assertNotEqual(state.state_digest, bootstrap_developed_state(_schematic(), run=other, portfolio_id="declared", branch_id="runner-v1", selection_decision_ref="decision:declared").selected_schematic.run_id)

    def test_malformed_packs_fail_typed(self) -> None:
        with self.assertRaises(ProjectRunnerError):
            SchematicPack.from_dict({"schema": "Other@1"})
        with self.assertRaises(ProjectRunnerError):
            ElementPack.from_dict({"schema": "ElementPack@1", "elements": [{"element_id": "a", "component_id": "c", "producer": "prism"}, {"element_id": "a", "component_id": "c", "producer": "prism"}]})
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "other", project_id="other", initial_state={"schema": "TestState@1"})
            with self.assertRaises(ProjectRunnerError):
                bootstrap_developed_state(_schematic(), run=repository.create_run("run-1"), portfolio_id="declared", branch_id="b", selection_decision_ref="decision:x")


class RunTests(unittest.TestCase):
    def _run(self, elements: ElementPack, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        repository = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = repository.create_run("run-1")
        pack = _schematic()
        options = _options(**overrides)
        state = bootstrap_developed_state(pack, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref)
        guard = _stage_guard(repository, run, pack, options)
        return repository, run_project(repository, run=run, stage_guard=guard, schematic=pack, elements=elements, seats=_seats(state.active_phase), levels=_levels(), grids=None, options=options)

    def test_two_seats_run_through_the_producer_with_receipts(self) -> None:
        repository, receipt = self._run(_elements())
        self.assertTrue(receipt["seat_execution_complete"], receipt["seat_results"])
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
        self.assertIn("portico-columns-top", datum_ids)                                        # handed over from the structure seat
        self.assertIn("level-cornice", datum_ids)
        record_names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
        self.assertTrue(any(name.startswith("seat-round-receipt-") for name in record_names))
        self.assertFalse(any(name.startswith("runner-stage-receipt-") for name in record_names))

    def test_realized_bounds_of_an_earlier_seat_exclude_a_later_opening(self) -> None:
        with self.assertRaises(ProjectRunnerError) as caught:
            self._run(_elements(opening_along=4.75))       # the door would open where a column stands
        self.assertIn("intersects exclusion", str(caught.exception))

    def test_owned_leaf_without_element_fails_typed_unless_relaxed(self) -> None:
        elements = ElementPack.from_dict({"schema": "ElementPack@1", "elements": [
            {"element_id": "wall-south", "component_id": "exterior-walls", "producer": "wall", "base_level": "level-ground",
             "params": {"origin": [12.0, 0.0], "direction": [-1, 0], "length": 12.0, "thickness": 0.6, "top_level": "level-cornice"}}]})
        with self.assertRaises(ProjectRunnerError):
            self._run(elements)
        repository, receipt = self._run(elements, strict_coverage=False)
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
            pack = _schematic()
            guard = _stage_guard(repository, other, pack, options)
            state = bootstrap_developed_state(pack, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref)
            with self.assertRaisesRegex(ProjectRunnerError, "another project or run"):
                run_project(repository, run=run, stage_guard=guard, schematic=pack, elements=_elements(), seats=_seats(state.active_phase), levels=_levels(), grids=None, options=options)
            record_names = [path.name for path in repository.layout.run("run-1").records.glob("*.json")]
            self.assertFalse(any(name.startswith("runner-schematic-pack-") for name in record_names))

    def test_cross_run_successor_requires_retained_satisfied_predecessor_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = FilesystemProjectRepository.initialize(Path(tmp) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
            predecessor_run = repository.create_run("run-0")
            run = repository.create_run("run-1")
            options = _options()
            pack = _schematic()
            state = bootstrap_developed_state(pack, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref)
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

            receipt = run_project(repository, run=run, stage_guard=guard, schematic=pack, elements=_elements(), seats=_seats(state.active_phase), levels=_levels(), grids=None, options=options)
            self.assertTrue(receipt["seat_execution_complete"])
            self.assertEqual(receipt["stage"]["stage_index"], 1)
            self.assertEqual(receipt["stage"]["status"], "OPEN")


def _ref(uri: str):
    from archflow.project.refs import ProjectRecordRef

    name = uri.rsplit("/", 1)[1]
    return ProjectRecordRef(project_id="demo", relative_path=f"runs/run-1/records/{name}", sha256=name.rsplit("-", 1)[1].split(".json")[0], media_type="application/json")


if __name__ == "__main__":
    unittest.main()
