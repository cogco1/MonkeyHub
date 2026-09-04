"""A real P036 project, shaped like the villa's but small enough to read.

Nothing here is a mock. The fixture initializes a project through
``FilesystemProjectRepository``, authors one State Record, retains it, and
retains the runner receipt a real run would leave, so the tests measure the
kernel's own answers rather than a rehearsal of the API's.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

# Importing the API package first puts the repository root on ``sys.path``;
# the fixture then reaches the kernel the same way the service does.
import archflow_studio_api  # noqa: F401

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.inputs import load_authored_record
from archflow.project.record_kinds import (
    PROJECT_STAGE_WORKFLOW,
    PROMOTION_DECISION,
    RUNNER_RUN_RECEIPT,
    SEAT_RHINO_EXECUTION,
    STATE_RECORD,
)
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.stage_workflow import DesignPhase
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow
from archflow.state.state_record import StateRecord, developed_design_view

PROJECT_ID = "demo-project"
REFERENCE_RUN_ID = "run-001"
HARNESS_RUN_ID = "run-002"
RUNNER_RECORD_PATH = "input/runner/state-record.json"
RUNNER_SEATS_PATH = "input/runner/seats.json"
EVIDENCE = "evidence:demo"

# The people the runner seats, authored where the runner reads them. One
# non-reviewer seat owning the one component the record's elements belong to:
# enough for ``run_project`` to compile a program, check the support relation
# and leave a real receipt, and small enough to read in a failure message.
SEATS_PAYLOAD: dict[str, object] = {
    "schema": "RunnerSeats@1",
    "commitment_ref": "commitment:demo",
    "branch_id": "runner-v1",
    "provider_identity": {
        "provider_id": "studio-fixture",
        "model_id": "deterministic",
        "provider_version": "2026-09-03",
        "provider_fingerprint": "0" * 64,
    },
    "seats": [
        {
            "seat_id": "seat-portico",
            "disciplines": ["structure_support"],
            "phases": ["design_development"],
            "owned_component_ids": ["portico"],
            "consumes": [],
            "reviewer": False,
        }
    ],
}

# What a Rhino seat's receipt carries about the program it executed. These are
# opaque identifiers on the wire: the tests assert they travel, not what they
# mean.
RHINO_RECEIPT_SCHEMA = "RhinoCadExecutionReceipt@4"
RHINO_BRANCH_ID = "runner-v1"
RHINO_BRANCH_EPOCH = 1
RHINO_PROGRAM_DIGEST = "b" * 64
RHINO_DESIGN_STATE_DIGEST = "c" * 64
PROGRAM_RECORD_SHA = "d" * 64

# "compute the digest the way production does" — distinct from ``None``, which
# is a receipt that deliberately claims no digest at all.
COMPUTED = object()

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
                "semantic_kind": "controlled-entry",
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
                "references": {"base": {"datum": "portico-base-top"}},
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
            "subject": "portico-base",
            "object": "portico-cornice",
            "propagation": "revalidate",
            "validator": {
                "check_kind": "support_contact",
                "tolerance": 0.001,
            },
        }
    ],
}


# The same record with nothing declared beyond its entities: no parameters, no
# relations, and so no dependency edges. What a project looks like before
# anyone has said how its quantities relate.
STRIPPED_RECORD_PAYLOAD: dict[str, object] = {
    key: value
    for key, value in RECORD_PAYLOAD.items()
    if key not in {"parameters", "relations"}
}


# The villa's shape, in the small: a portico with two sub-components under it —
# one whose element carries ``height``, and one that has no element at all.
# That asymmetry is the whole reproduction. ``portico-columns`` is what the
# architect means and what the record has no control for; the west abutment
# under ``portico-roofs`` is the neighbour whose ``height`` a resolver scoring
# on field names would have changed instead.
#
# It is a second payload rather than an edit of ``RECORD_PAYLOAD`` because the
# projection tests count that record's components and elements: a fixture that
# quietly moved those numbers would leave those tests measuring a different
# building than the one they describe.
PORTICO_RECORD_PAYLOAD: dict[str, object] = {
    **{key: value for key, value in RECORD_PAYLOAD.items() if key != "entities"},
    "entities": [
        *RECORD_PAYLOAD["entities"],  # type: ignore[misc]
        {
            "entity_id": "portico-roofs",
            "schema": "Component@1",
            "parent_id": "portico",
            "fields": {
                # Registered aliases, like every other component here: the
                # record refuses a semantic term the registry does not declare
                # (ADR-006), and a fixture may not invent one either.
                "semantic_kind": "roof",
                "intent": "the portico roofs and their abutments",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "portico-columns",
            "schema": "Component@1",
            "parent_id": "portico",
            "fields": {
                "semantic_kind": "support",
                "intent": "the portico columns, drawn but not yet controlled",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "portico-roof-abutment-west",
            "schema": "Element@1",
            "parent_id": "portico-roofs",
            "fields": {
                "component_id": "portico-roofs",
                "producer": "prism",
                "references": {"base": {"datum": "portico-base-top"}},
                "params": {
                    "profile": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "height": 0.45,
                },
            },
            "basis_refs": [EVIDENCE],
        },
    ],
}


# The seat pack for that record: the same one seat, owning the portico and the
# two components under it, so the runner can still compile a program for it.
PORTICO_SEATS_PAYLOAD: dict[str, object] = {
    **{key: value for key, value in SEATS_PAYLOAD.items() if key != "seats"},
    "seats": [
        {
            **SEATS_PAYLOAD["seats"][0],  # type: ignore[index]
            "owned_component_ids": ["portico", "portico-roofs", "portico-columns"],
        }
    ],
}


def run_records(run_id: str) -> PersistenceDestination:
    """The run-record area of one run."""

    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def missing_workflow_ref(run_id: str) -> str:
    """A well-formed record URI for a workflow record that is not there."""

    return (
        f"project://{PROJECT_ID}/runs/{run_id}/records/"
        f"project-stage-workflow-{'a' * 64}.json"
    )


def runner_state_digest(
    repository: FilesystemProjectRepository,
    run_id: str,
    payload: object = RECORD_PAYLOAD,
) -> str:
    """The ``design_state_digest`` the runner would write for that run.

    Computed straight from the kernel, with the runner's own view kwargs, so a
    test comparing the API against it is comparing against production, not
    against the API repeating itself. ``payload`` names which record the
    project authored, for a fixture that authored something other than the
    default one.
    """

    run = RunRef(PROJECT_ID, run_id, repository.read_head())
    record = StateRecord.from_dict(payload).bound_to(run)
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


def write_runner_seats(
    repository: FilesystemProjectRepository,
    payload: object = SEATS_PAYLOAD,
) -> Path:
    """Author the seat pack where the runner reads it: input, not a record."""

    path = repository.layout.resolve_relative(RUNNER_SEATS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def retain_runner_receipt(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    design_state_digest: str | None,
    workflow_ref: str | None = None,
    record_payload: object | None = None,
    seat_results: object | None = None,
) -> ProjectRecordRef:
    """Retain the receipt a completed runner run leaves behind.

    ``design_state_digest=None`` writes a receipt that claims no digest, which
    is what an older or interrupted runner can leave.  Like the production
    runner, the fixture first retains the State Record bound to this exact run
    and base, then points the receipt at that immutable record.
    """

    authored = (
        load_authored_record(repository).record
        if record_payload is None
        else StateRecord.from_dict(record_payload)
    )
    record = authored.bound_to(run)
    state_record_ref = repository.put_json(
        run=run,
        destination=run_records(run.run_id),
        record_kind=STATE_RECORD,
        payload=record.to_dict(),
    )
    payload: dict[str, object] = {
        "schema": "RunnerRunReceipt@3",
        "project_id": PROJECT_ID,
        "run_id": run.run_id,
        "seat_execution_complete": True,
        "state_record_ref": state_record_ref.uri,
        "state_record_digest": record.digest,
    }
    if design_state_digest is not None:
        payload["design_state_digest"] = design_state_digest
    if workflow_ref is not None:
        payload["workflow_ref"] = workflow_ref
    if seat_results is not None:
        payload["seat_results"] = seat_results
    return repository.put_json(
        run=run,
        destination=run_records(run.run_id),
        record_kind=RUNNER_RUN_RECEIPT,
        payload=payload,
    )


def retain_rhino_receipt(
    repository: FilesystemProjectRepository,
    run: RunRef,
    *,
    stage_id: str,
    file_name: str,
    payload_bytes: bytes,
    workspace_subdir: str | None = None,
    status: str = "succeeded",
    inspection: bool = True,
    readback_verified: bool | None = None,
) -> ProjectRecordRef:
    """Write an exported file and retain the receipt that certifies it.

    This is what a Rhino seat leaves behind: the bytes in the run's export
    workspace and a ``seat-rhino-execution`` record naming their digest, the
    run's base and the program that produced them. ``workspace_subdir`` puts the
    file somewhere other than the ``cad-<stage_id>`` convention — older runs did
    — and ``inspection=False`` writes the receipt a failed export leaves, which
    claims no digest at all.

    ``readback_verified`` follows ``status`` unless a test states it, so a
    receipt that failed and still inspected its file is written on purpose
    rather than by the default's accident.
    """

    directory = repository.layout.run(run.run_id).workspaces / Path(
        workspace_subdir or f"cad-{stage_id}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file_name).write_bytes(payload_bytes)
    program_path = (
        f"runs/{run.run_id}/branches/{RHINO_BRANCH_ID}/records/"
        f"{stage_id}-geometry-program-{PROGRAM_RECORD_SHA}.json"
    )
    payload: dict[str, object] = {
        "schema": RHINO_RECEIPT_SCHEMA,
        "status": status,
        "readback_verified": (
            (status == "succeeded")
            if readback_verified is None
            else readback_verified
        ),
        "artifact_relative_path": file_name,
        "identity": {
            "schema": "RhinoCadExportIdentity@2",
            "length_unit": "meter",
            "up_axis": "Z-up",
            "binding": {
                "schema": "RhinoCadProgramBinding@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "stage_id": stage_id,
                "branch_id": RHINO_BRANCH_ID,
                "branch_epoch": RHINO_BRANCH_EPOCH,
                "design_state_digest": RHINO_DESIGN_STATE_DIGEST,
                "program_digest": RHINO_PROGRAM_DIGEST,
                "program_ref": {
                    "media_type": "application/json",
                    "project_id": PROJECT_ID,
                    "relative_path": program_path,
                    "sha256": PROGRAM_RECORD_SHA,
                    "uri": f"project://{PROJECT_ID}/{program_path}",
                },
                "base": {
                    "project_id": PROJECT_ID,
                    "version": run.base.version,
                    "state_sha256": run.base.state_sha256,
                },
            },
        },
        "inspection": (
            {
                "schema": "RhinoCadInspection@2",
                # File identity, computed over the bytes just written: the
                # receipt claims a digest only when the export succeeded.
                "file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "file_bytes": len(payload_bytes),
                "object_count": 1,
            }
            if inspection
            else None
        ),
    }
    return repository.put_json(
        run=run,
        destination=run_records(run.run_id),
        record_kind=SEAT_RHINO_EXECUTION,
        payload=payload,
    )


def make_project(
    root: Path,
    *,
    design_state_digest: object = COMPUTED,
) -> tuple[FilesystemProjectRepository, ProjectRecordRef]:
    """One initialized project with the authored record and one finished run.

    ``design_state_digest`` replaces what the retained receipt claims the run
    executed — another digest, or ``None`` for a receipt that claims none — so
    a test can watch the projection disagree with a receipt, or decline to.
    """

    project_dir = Path(root) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    run = repository.create_run(REFERENCE_RUN_ID)
    write_runner_record(repository)
    write_runner_seats(repository)
    retain_runner_receipt(
        repository,
        run,
        design_state_digest=(
            runner_state_digest(repository, REFERENCE_RUN_ID)
            if design_state_digest is COMPUTED
            else design_state_digest
        ),
    )
    record_ref = next(
        ref
        for ref in repository.list_json(
            run=run, destination=run_records(REFERENCE_RUN_ID)
        )
        if ref.record_kind == STATE_RECORD
    )
    return repository, record_ref


def make_portico_project(root: Path) -> tuple[FilesystemProjectRepository, str]:
    """The reproduction's project: the portico, its roofs and its columns.

    The same shape ``make_project`` builds — a real P036 project, one authored
    record, one finished run and the receipt it left — authored with the record
    that has a component nothing under it can edit. Answers the repository and
    the ``stateDigest`` every request against it has to carry.
    """

    project_dir = Path(root) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    run = repository.create_run(REFERENCE_RUN_ID)
    write_runner_record(repository, PORTICO_RECORD_PAYLOAD)
    write_runner_seats(repository, PORTICO_SEATS_PAYLOAD)
    digest = runner_state_digest(
        repository, REFERENCE_RUN_ID, PORTICO_RECORD_PAYLOAD
    )
    retain_runner_receipt(
        repository,
        run,
        design_state_digest=digest,
        record_payload=PORTICO_RECORD_PAYLOAD,
    )
    return repository, digest


def make_empty_project(root: Path) -> FilesystemProjectRepository:
    """A project with the authored record and no run at all."""

    project_dir = Path(root) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    write_runner_record(repository)
    write_runner_seats(repository)
    return repository


def add_later_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str,
    workflow_id: str | None = None,
    workflow_ref: str | None = None,
) -> RunRef:
    """A complete run whose receipt is stamped newer than everything else.

    ``workflow_id`` retains a real ``ProjectStageWorkflow`` and points the
    receipt at it; ``workflow_ref`` points the receipt at a URI verbatim, which
    is how a test builds a reference that will not resolve. The mtime is forced
    forward so "newest complete receipt" would pick this run unless the rule
    itself excludes it — the test then fails for the reason it names.
    """

    run = repository.create_run(run_id)
    if workflow_id is not None:
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
            record_kind=PROJECT_STAGE_WORKFLOW,
            payload=workflow.to_dict(),
        ).uri
    receipt_ref = retain_runner_receipt(
        repository,
        run,
        design_state_digest=runner_state_digest(repository, run_id),
        workflow_ref=workflow_ref,
    )
    receipt_path = repository.layout.resolve_record(receipt_ref)
    newest = receipt_path.stat().st_mtime + 60.0
    os.utime(receipt_path, (newest, newest))
    return run


def add_harness_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str = HARNESS_RUN_ID,
) -> RunRef:
    """A newer, complete run whose workflow says it is a harness, not the design."""

    return add_later_run(
        repository, run_id=run_id, workflow_id="equivalence-harness"
    )


def advance_head(
    repository: FilesystemProjectRepository,
    *,
    run_id: str = "run-promotion",
) -> ProjectVersionRef:
    """Move the project's canonical HEAD forward through P036's own path.

    Promotion is the kernel's and nobody else's: a run based on the current
    HEAD, an accepted ``PromotionDecision@1`` naming the exact state it
    checked, then ``prepare_transition`` and ``compare_and_swap``. The Studio
    API may never do any of this — which is precisely why a test has to, to
    see what the API says once the version it validated against has stopped
    being current.
    """

    base = repository.read_head()
    run = repository.create_run(run_id)
    decision = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW, run_id=run_id
        ),
        record_kind=PROMOTION_DECISION,
        payload={
            "schema": "PromotionDecision@1",
            "status": "accepted",
            "project_id": PROJECT_ID,
            "run_id": run_id,
            "checked_state": {
                "project_id": base.project_id,
                "version": base.version,
                "state_sha256": base.require_digest(),
            },
            "candidate_ref": (
                f"project://{PROJECT_ID}/{RUNNER_RECORD_PATH}"
            ),
        },
    )
    prepared = repository.prepare_transition(
        run=run,
        expected=base,
        replacement_state={
            "project_id": PROJECT_ID,
            "version": base.version + 1,
        },
        decision_receipt=decision,
    )
    return repository.compare_and_swap(
        expected=prepared.expected,
        event=prepared.event,
        replacement=prepared.replacement,
    )


def add_unreadable_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str = "broken-run",
) -> str:
    """A run directory with no manifest: the repository cannot read it at all."""

    (repository.layout.runs / run_id).mkdir(parents=True, exist_ok=True)
    return run_id


def unlistable_run(
    repository: FilesystemProjectRepository,
    *,
    run_id: str,
) -> str:
    """A run whose manifest is fine and whose records the repository refuses.

    Different from a directory with no manifest: this run *loads*, so nothing
    refuses it before its records are read. It is what a half-written or
    tampered-with run looks like from the outside, and it is the case the
    reference-run rule is tolerant of and the named-run path was not.
    """

    for path in repository.layout.run(run_id).records.glob("*.json"):
        path.write_text("{ not a record", encoding="utf-8")
    return run_id


# ---- a fake codex, of the real one's shape ---------------------------------

# What the shim answers ``codex --version`` with. Building a CodexCompiler
# asks for it once, and every receipt that compiler mints is signed with it.
CODEX_SHIM_VERSION = "codex-cli 0.0.0-test"


def write_codex_shim(
    directory: Path,
    *,
    answer: str | None = None,
    hang_seconds: float | None = None,
) -> tuple[Path, Path]:
    """A fake ``codex``, shaped like the real one on this machine.

    ``codex`` here is a shim (``codex.cmd`` -> ``cmd.exe`` -> ``node``), so a
    fake that is one process proves nothing about killing a hung agent. This
    one is a script over a Python grandchild: the grandchild either writes
    ``answer`` to stdout and exits, or holds stdout open and sleeps for
    ``hang_seconds``, which is exactly the case that outlives a kill that does
    not take the tree.

    ``--version`` is answered by the script itself and immediately, because
    that is what building a compiler asks it, and a version probe that hung
    would be measuring the wrong thing.

    Returns the shim's path and the file the grandchild writes its pid to.
    """

    if (answer is None) == (hang_seconds is None):
        raise ValueError("a shim either answers or hangs, and says which")
    pid_file = directory / "grandchild.pid"
    grandchild = directory / "agent.py"
    body = [
        "import os, sys, time",
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))",
    ]
    if answer is not None:
        body.append(f"sys.stdout.write({answer!r})")
        body.append("sys.stdout.flush()")
    else:
        body.append("sys.stdout.write('started')")
        body.append("sys.stdout.flush()")
        body.append(f"time.sleep({hang_seconds})")
    grandchild.write_text("\n".join(body) + "\n", encoding="utf-8")
    if os.name == "nt":
        shim = directory / "codex.cmd"
        shim.write_text(
            "@echo off\r\n"
            'if "%1"=="--version" (\r\n'
            f"  echo {CODEX_SHIM_VERSION}\r\n"
            "  exit /b 0\r\n"
            ")\r\n"
            f'"{sys.executable}" "{grandchild}"\r\n',
            encoding="utf-8",
        )
    else:
        shim = directory / "codex"
        shim.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then\n'
            f'  echo "{CODEX_SHIM_VERSION}"\n'
            "  exit 0\n"
            "fi\n"
            f'"{sys.executable}" "{grandchild}"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim, pid_file
