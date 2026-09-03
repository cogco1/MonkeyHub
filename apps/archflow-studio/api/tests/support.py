"""A real P036 project, shaped like the villa's but small enough to read.

Nothing here is a mock. The fixture initializes a project through
``FilesystemProjectRepository``, authors one State Record, retains it, and
retains the runner receipt a real run would leave, so the tests measure the
kernel's own answers rather than a rehearsal of the API's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Importing the API package first puts the repository root on ``sys.path``;
# the fixture then reaches the kernel the same way the service does.
import archflow_studio_api  # noqa: F401

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.design_maturity import DesignPhase
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow
from archflow.state.state_record import StateRecord, developed_design_view

PROJECT_ID = "demo-project"
REFERENCE_RUN_ID = "run-001"
HARNESS_RUN_ID = "run-002"
RUNNER_RECORD_PATH = "input/runner/state-record.json"
EVIDENCE = "evidence:demo"

# The kwargs the project runner passes to ``developed_design_view``; the
# projection has to reproduce this exact view or its digest names nothing.
VIEW_KWARGS = {
    "portfolio_id": "declared-schematic",
    "branch_id": "runner-v1",
    "selection_decision_ref": "decision:declared-schematic-selection",
}

# Two components (one nested), one level, one grid axis, two elements with real
# references, three parameters (one locked, two derived) and one support
# relation with a validator: closure, locks and three-state checks all have
# something to say about this record.
RECORD_PAYLOAD: dict[str, object] = {
    "schema": "StateRecord@1",
    "project_id": PROJECT_ID,
    "run_id": "runner",
    "evidence_refs": [EVIDENCE],
    "option": {
        "option_id": "option-demo",
        "label": "demo option",
        "typology": "villa",
        "rationale": "fixture",
        "footprint_cells": [],
        "assumption_refs": [],
    },
    "entities": [
        {
            "entity_id": "building",
            "schema": "Component@1",
            "fields": {
                "semantic_kind": "building",
                "intent": "the demo building",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "portico",
            "schema": "Component@1",
            "parent_id": "building",
            "fields": {
                "semantic_kind": "portico",
                "intent": "the demo portico",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "level-ground",
            "schema": "Level@1",
            "fields": {"role": "ground", "elevation": 0.0},
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "axis-a",
            "schema": "GridAxis@1",
            "fields": {
                "role": "front",
                "origin": [0.0, 0.0, 0.0],
                "direction": [0.0, 0.0, 1.0],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "portico-base",
            "schema": "Element@1",
            "parent_id": "portico",
            "fields": {
                "component_id": "portico",
                "producer": "prism",
                "references": {"base": {"level": "level-ground"}},
                "params": {
                    "profile": [[0, 0], [4, 0], [4, 2], [0, 2]],
                    "height": 0.6,
                },
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "portico-cornice",
            "schema": "Element@1",
            "parent_id": "portico",
            "fields": {
                "component_id": "portico",
                "producer": "prism",
                "references": {
                    "base": {
                        "offset_from": {
                            "level": "level-ground",
                            "offset": 0.6,
                        }
                    }
                },
                "params": {
                    "profile": [[0, 0], [4, 0], [4, 2], [0, 2]],
                    "height": 0.3,
                },
            },
            "basis_refs": [EVIDENCE],
        },
    ],
    "parameters": [
        {
            "key": "module",
            "value": 1.2,
            "unit": "m",
            "lock_authority": "client",
        },
        {
            "key": "bay",
            "value": 2.4,
            "unit": "m",
            "expr": "2 * module",
            "inputs": ["module"],
        },
        {
            "key": "span",
            "value": 4.8,
            "unit": "m",
            "expr": "2 * bay",
            "inputs": ["bay"],
        },
    ],
    "relations": [
        {
            "relation_id": "rel-cornice-on-base",
            "kind": "support",
            "subject": "portico-cornice",
            "object": "portico-base",
            "propagation": "revalidate",
            "validator": {
                "check_kind": "support_contact",
                "tolerance": 0.001,
            },
        }
    ],
}


def run_records(run_id: str) -> PersistenceDestination:
    """The run-record area of one run."""

    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def runner_state_digest(
    repository: FilesystemProjectRepository,
    run_id: str,
) -> str:
    """The ``design_state_digest`` the runner would write for that run.

    Computed straight from the kernel, with the runner's own view kwargs, so a
    test comparing the API against it is comparing against production, not
    against the API repeating itself.
    """

    run = RunRef(PROJECT_ID, run_id, repository.read_head())
    record = StateRecord.from_dict(RECORD_PAYLOAD).bound_to(run)
    return developed_design_view(record, run=run, **VIEW_KWARGS).state_digest


def write_runner_record(
    repository: FilesystemProjectRepository,
    payload: object = RECORD_PAYLOAD,
) -> Path:
    """Author the record where the runner reads it: it is input, not a record."""

    path = repository.layout.resolve_relative(RUNNER_RECORD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def retain_runner_receipt(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    design_state_digest: str,
    workflow_ref: str | None = None,
) -> ProjectRecordRef:
    """Retain the receipt a completed runner run leaves behind."""

    payload: dict[str, object] = {
        "schema": "RunnerRunReceipt@3",
        "project_id": PROJECT_ID,
        "run_id": run.run_id,
        "seat_execution_complete": True,
        "design_state_digest": design_state_digest,
    }
    if workflow_ref is not None:
        payload["workflow_ref"] = workflow_ref
    return repository.put_json(
        run=run,
        destination=run_records(run.run_id),
        record_kind="runner-run-receipt",
        payload=payload,
    )


def make_project(
    root: Path,
    *,
    design_state_digest: str | None = None,
) -> tuple[FilesystemProjectRepository, ProjectRecordRef]:
    """One initialized project with the authored record and one finished run.

    ``design_state_digest`` overrides what the retained receipt claims the run
    executed, so a test can watch the projection disagree with a receipt.
    """

    project_dir = Path(root) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    run = repository.create_run(REFERENCE_RUN_ID)
    record_ref = repository.put_json(
        run=run,
        destination=run_records(REFERENCE_RUN_ID),
        record_kind="state-record",
        payload=RECORD_PAYLOAD,
    )
    write_runner_record(repository)
    retain_runner_receipt(
        repository,
        run,
        design_state_digest=(
            design_state_digest
            if design_state_digest is not None
            else runner_state_digest(repository, REFERENCE_RUN_ID)
        ),
    )
    return repository, record_ref


def make_empty_project(root: Path) -> FilesystemProjectRepository:
    """A project with the authored record and no run at all."""

    project_dir = Path(root) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    write_runner_record(repository)
    return repository


def add_harness_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str = HARNESS_RUN_ID,
    workflow_id: str = "equivalence-harness",
) -> RunRef:
    """A newer, complete run whose workflow says it is a harness, not the design.

    Its receipt is stamped strictly newer than every other file in the project
    so "newest complete receipt" would pick it if the rule did not exclude
    harness workflows.
    """

    run = repository.create_run(run_id)
    workflow = ProjectStageWorkflow(
        project_id=PROJECT_ID,
        workflow_id=workflow_id,
        stages=(
            ProjectStage(
                stage_id="stage-0",
                stage_index=0,
                phase=DesignPhase.DESIGN_DEVELOPMENT,
                required_roles=("seat-structure",),
                required_checks=("relation-check",),
                close_obligation_id="obligation-close-stage-0",
            ),
        ),
    )
    workflow_ref = repository.put_json(
        run=run,
        destination=run_records(run_id),
        record_kind="project-stage-workflow",
        payload=workflow.to_dict(),
    )
    receipt_ref = retain_runner_receipt(
        repository,
        run,
        design_state_digest=runner_state_digest(repository, run_id),
        workflow_ref=workflow_ref.uri,
    )
    receipt_path = repository.layout.resolve_record(receipt_ref)
    newest = receipt_path.stat().st_mtime + 60.0
    os.utime(receipt_path, (newest, newest))
    return run
