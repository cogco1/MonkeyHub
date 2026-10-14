"""A disposable P036 project that holds a fixture the way production holds a design: a stage run, a runner receipt, decisions.

The tools the repository ships do the work, each as its own process:

1. ``freeze_project_stage_workflow.py`` retains a one-stage workflow;
2. ``open_stage_run.py`` opens stage 0 as a run;
3. ``run_project.py --export`` runs the fixture's record through the runner and the OCCT exporter.

After that the project holds a retained design run with its receipt, so the
Project Runtime (in process) can record decisions against it and answer the
context read for it. Rejected proposals from the history are recorded through
``POST /api/decisions``. Nothing is added to a state file by hand: whether a
rejection reaches the agent's context is whatever ``POST /api/intents/context``
returns.

The project lives under an explicit scratch root the caller owns. Nothing is
issued, and HEAD is never moved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.stage_workflow import DesignPhase, ProjectStage, ProjectStageWorkflow
from archflow.state.state_record import StateRecord

from labs.revision_tasks import _bootstrap

TOOLS = _bootstrap.CHECKOUT / "tools" / "project"
RECORD_PATH = "input/runner/state-record.json"
SEATS_PATH = "input/runner/seats.json"
STAGE_RUN = "stage-0-001"


class ProductionProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductionProject:
    project_dir: Path
    run_id: str
    state_digest: str


def _tool(script: str, *args: str) -> str:
    done = subprocess.run([sys.executable, str(TOOLS / script), *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ProductionProjectError(f"{script} failed ({done.returncode}): {(done.stderr or done.stdout)[-1500:]}")
    return done.stdout


def _first_json(text: str) -> dict[str, Any]:
    start = text.index("{")
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return json.loads(text[start:index + 1])
    raise ProductionProjectError("no JSON object in tool output")


def make_production_project(record: StateRecord, root: Path) -> ProductionProject:
    """Initialize the project, freeze a one-stage workflow, open it and run the record through the runner."""

    project_dir = root / record.project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    repository = FilesystemProjectRepository.initialize(project_dir, project_id=record.project_id,
                                                        initial_state={"project_id": record.project_id, "version": 0})
    for relative, payload in ((RECORD_PATH, record.to_dict()), (SEATS_PATH, _seats(record))):
        path = repository.layout.resolve_relative(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    workflow = ProjectStageWorkflow(
        project_id=record.project_id, workflow_id="revision-task-workflow",
        stages=(ProjectStage(stage_id="stage-0", stage_index=0, phase=DesignPhase.DESIGN_DEVELOPMENT,
                             required_roles=("geometry-program", "model-inspection"), required_checks=(),
                             close_obligation_id="close-stage-0"),),
        basis_refs=("decision:revision-task-workflow",))
    workflow_file = root / "workflow.json"
    workflow_file.write_text(json.dumps(workflow.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    frozen = _first_json(_tool("freeze_project_stage_workflow.py", "--project", str(project_dir), "--run", "workflow-001",
                               "--workflow", str(workflow_file), "--create-run"))
    opened = _first_json(_tool("open_stage_run.py", "--project", str(project_dir), "--workflow-ref", frozen["workflow_ref"],
                               "--stage-index", "0", "--run", STAGE_RUN))
    envelope_ref = opened.get("stage_envelope_ref") or opened.get("envelope_ref")
    if not envelope_ref:
        raise ProductionProjectError(f"open_stage_run gave no envelope ref: {sorted(opened)}")
    _tool("run_project.py", "--project", str(project_dir), "--run", STAGE_RUN, "--workflow-ref", frozen["workflow_ref"],
          "--stage-envelope-ref", envelope_ref, "--export", "--workspace", str(root / "runner-workspace"))
    return ProductionProject(project_dir, STAGE_RUN, "")


def _seats(record: StateRecord) -> dict[str, Any]:
    components = sorted(e.entity_id for e in record.entities_of("Component@1"))
    return {"schema": "RunnerSeats@1", "commitment_ref": "commitment:revision-task", "branch_id": "runner-v1",
            "provider_identity": {"provider_id": "revision-task-fixture", "model_id": "deterministic", "provider_version": "1",
                                  "provider_fingerprint": "0" * 64},
            "seats": [{"seat_id": "seat-design", "disciplines": ["structure_support"], "phases": ["design_development"],
                       "owned_component_ids": components, "consumes": [], "reviewer": False}]}


def record_rejections_and_read_context(project: ProductionProject, rejections: list[dict[str, Any]], *, utterance: str,
                                       element_ids: tuple[str, ...], cache_dir: Path) -> dict[str, Any]:
    """Record each rejected proposal as a decision against the stage run, then read the context pack for that run."""

    from fastapi.testclient import TestClient
    from project_runtime.main import create_app
    from project_runtime.settings import StudioSettings

    app = create_app(StudioSettings(cad_export="off", project_dir=project.project_dir, cache_dir=cache_dir))
    with TestClient(app) as client:
        state = client.get("/api/state", params={"run": project.run_id})
        if state.status_code != 200:
            raise ProductionProjectError(f"state read failed: {state.text[:400]}")
        digest = state.json()["stateDigest"]
        project_id = state.json()["projectId"]
        recorded = []
        for rejection in rejections:
            body = {"projectId": project_id, "rawLanguage": rejection["text"], "disposition": "reject", "strength": "hard",
                    "targetRef": rejection["target_ref"], "scope": {"domain": "design", "extent": "project"},
                    "source": {"kind": "design", "sourceRunId": project.run_id, "stateDigest": digest},
                    "applicability": "scope", "sourceKind": "human"}
            answer = client.post("/api/decisions", json=body)
            recorded.append({"status": answer.status_code, "body": answer.json() if answer.headers.get("content-type", "").startswith("application/json") else answer.text})
        pack = client.post("/api/intents/context", json={"projectId": project_id, "sourceRunId": project.run_id, "stateDigest": digest,
                                                         "utterance": utterance, "elementIds": list(element_ids)})
        if pack.status_code != 200:
            raise ProductionProjectError(f"context read failed: {pack.text[:400]}")
        return {"state_digest": digest, "decisions": recorded, "pack": pack.json()}
