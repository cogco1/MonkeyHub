"""The project runner's demo block, and a project an export writes into.

One State Record (components and massing, three levels, four grid lines, two
element rows), the three seats and run options the runner reads, a retained
one-stage workflow guard, and ``_ExportProject``: one project, one run and the
per-seat workspaces an export writes into. Nothing is a mock; the runner runs
for real.

These are the runner integration tests' own fixtures
(tests/integration/test_project_runner.py, which also drives tools.project's
stage commands and MonkeyMonitor's usage rows and so stays in the repository
suite). The MonkeyArch tests that run the runner through CAD, an edit or a
record keep this copy: a package's tests cannot import the repository suite.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from monkeyarch.domain.discipline_seats import DeclarationQuadrant, SeatSpec
from monkeyarch.application.geometry_proposal import GeometryProposalProviderIdentity
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import PROJECT_STAGE_WORKFLOW, STAGE_RUN_ENVELOPE
from monkeyarch.application.project_runner import RunOptions, StageExecutionGuard, run_project
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.state_record import Entity, StateRecord, developed_design_view
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow, open_stage_run_envelope
from archflow.project.refs import record_ref_from_uri

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

    return (patch.multiple("monkeycad.backends.rhino.export", prepare_rhino_three_dm_export=_refuse_rhino, execute_rhino_three_dm_export=_refuse_rhino),
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

    def run_once(self, record: StateRecord | None = None, *, operation_observer=None) -> dict:
        record = record if record is not None else self.record
        guard = _stage_guard(self.repository, self.run, record, self.options)
        return run_project(self.repository, run=self.run, stage_guard=guard, record=record, seats=_seats(DesignPhase.DESIGN_DEVELOPMENT),
                           options=self.options, operation_observer=operation_observer)

    def records(self, prefix: str) -> list[Path]:
        return sorted(self.repository.layout.run("run-1").records.glob(f"{prefix}-*.json"))


try:
    from monkeycad.backends.occt import build as _occt_build  # noqa: F401 - the runner suite's own guard
    from monkeycad.backends.occt.kernel import occt_available
    from monkeycad.backends.occt.measure import measure_shape  # noqa: F401
    from monkeycad.backends.occt.step import read_step  # noqa: F401
    _OCCT = occt_available()
except Exception:  # the backend is optional; the tests below say so
    _OCCT = False
NEEDS_OCCT = unittest.skipUnless(_OCCT, "cadquery-ocp is not installed")


def run_source(self, project, record, run_id, source=None, *, one_seat=True, required_checks=None, operation_observer=None):
    """Run ``record`` in ``run_id`` of the export project, from ``source``'s receipt when given.

    ``IncrementalSourceRunTests.run_source`` in the runner's integration tests; the tests
    that run a source record bind it as a method (``_run = run_source``).
    """

    run = project.run if run_id == project.run.run_id else project.repository.create_run(run_id)
    options = replace(project.options, workspace_root=project.repository.layout.run(run_id).workspaces,
                      source_run_receipt_ref=None if source is None else record_ref_from_uri(source["receipt_ref"], "demo"))
    seats = _seats(DesignPhase.DESIGN_DEVELOPMENT)
    if one_seat:
        seats = (replace(seats[0], owned_component_ids=("building",)), seats[-1])
    for seat in seats:
        if not seat.reviewer:
            (options.workspace_root / f"cad-stage-0-test-production-{seat.seat_id}").mkdir(parents=True, exist_ok=True)
    return run_project(project.repository, run=run, record=record, seats=seats, options=options, operation_observer=operation_observer,
                       stage_guard=_stage_guard(project.repository, run, record, options,
                                                **({"required_checks": required_checks} if required_checks is not None else {})))


def _taller_plinth(record):
    return replace(record, entities=tuple(
        replace(entity, fields={**entity.fields, "params": {**entity.fields["params"], "height": 0.8}})
        if entity.entity_id == "columns-plinth" else entity
        for entity in record.entities
    ))
