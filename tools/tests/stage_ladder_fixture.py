"""The runner's demo record in a project with a frozen stage ladder, and a stage run the way the tools open it.

``_ladder_project`` holds the demo record as the project's work in progress and
freezes a workflow with one stage per phase (``tools/project/freeze_project_stage_workflow``);
``_run_opened_stage`` executes a stage ``tools/project/open_stage_run`` opened, with
``tools/project/run_project``'s own stage guard. These are the runner integration
tests' builders (tests/integration/test_project_runner.py), copied for the tools'
tests: a tool's tests cannot import the repository suite.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from monkeyarch.domain.discipline_seats import DeclarationQuadrant, SeatSpec
from monkeyarch.application.geometry_proposal import GeometryProposalProviderIdentity
from archflow.project.repository import FilesystemProjectRepository
from monkeyarch.application.project_runner import RunOptions, run_project
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline
from archflow.state.state_record import Entity, StateRecord
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow
from archflow.project.inputs import load_authored_record
from tools.project.freeze_project_stage_workflow import freeze_workflow
from tools.project.run_project import _stage_guard as tool_stage_guard

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


def _ladder_project(root: Path, phases: tuple[DesignPhase, ...], workflow_id: str = "demo-two-stage",
                    record: StateRecord | None = None) -> tuple[FilesystemProjectRepository, str]:
    """A project holding the demo record (or ``record``) as its WIP and a frozen workflow with one stage per phase; answers the workflow ref."""

    repository = FilesystemProjectRepository.initialize(root, project_id="demo", initial_state={"schema": "TestState@1"})
    authored = repository.layout.authored_record
    authored.parent.mkdir(parents=True, exist_ok=True)
    authored.write_text(json.dumps((record or _record()).to_dict()), encoding="utf-8")
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
    """Execute a stage ``tools/project/open_stage_run`` opened, in the phase its retained envelope states."""

    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(str(opened["run_id"]))
    guard = tool_stage_guard(repository, run, workflow_uri=workflow_ref, envelope_uri=str(opened["stage_envelope_ref"]))
    record = load_authored_record(repository).record
    return run_project(repository, run=run, stage_guard=guard, record=record, seats=_seats(guard.envelope.phase), options=_options())


def _geometry_only(record: StateRecord) -> StateRecord:
    """The same record with no massing declared: no MassingLevel, Volume, Space or Connection, and no volume on any component."""

    massing = {"MassingLevel@1", "Volume@1", "Space@1", "Connection@1"}
    entities = tuple(
        replace(e, fields={**e.fields, "volume_ids": []}) if e.schema == "Component@1" and "volume_ids" in e.fields else e
        for e in record.entities if e.schema not in massing
    )
    return replace(record, entities=entities, relations=tuple(r for r in record.relations
                                                               if {r.subject, r.object} <= {e.entity_id for e in entities}))
