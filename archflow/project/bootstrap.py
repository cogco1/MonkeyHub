"""Generic raw-request project bootstrap with no building-type knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.repository import FilesystemProjectRepository


@dataclass(frozen=True, slots=True)
class ProjectBootstrapResult:
    project_id: str
    run: RunRef
    request: ProjectRecordRef
    receipt: ProjectRecordRef
    head: ProjectVersionRef


def bootstrap_raw_request_project(
    root: Path,
    *,
    project_id: str,
    prompt: str,
    run_id: str = "bootstrap-001",
    synthetic_test: bool = False,
) -> ProjectBootstrapResult:
    """Create a project envelope and retain one raw request through its sinks."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty text")
    repository = FilesystemProjectRepository.initialize(
        root,
        project_id=project_id,
        initial_state={
            "schema": "CanonicalProjectState@1",
            "phase": "project_initialized",
            "authoritative_record_refs": [],
            "derived_record_refs": [],
        },
    )
    run = repository.create_run(run_id)
    request = repository.put_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
        record_kind="raw-request",
        payload={
            "schema": "RawProjectRequest@1",
            "prompt": prompt,
        },
    )
    receipt = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="project-bootstrap",
        payload={
            "schema": "ProjectBootstrapReceipt@1",
            "project_id": project_id,
            "run_id": run.run_id,
            "base": {
                "project_id": run.base.project_id,
                "version": run.base.version,
                "state_sha256": run.base.require_digest(),
            },
            "request_ref": request.uri,
            "synthetic_test": synthetic_test,
            "generation_authority": False,
            "architectural_usability_proven": False,
            "derived_design_available": False,
        },
    )
    repository.verify()
    return ProjectBootstrapResult(
        project_id=project_id,
        run=run,
        request=request,
        receipt=receipt,
        head=repository.read_head(),
    )
