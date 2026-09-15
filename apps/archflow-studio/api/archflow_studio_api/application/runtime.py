"""Read runtime recovery facts through the existing Studio and P036 readers.

This projection has no writer, replay, or worker-start capability. A caller
may use it after a worker exits; absence of a completed receipt is then an
interrupted operation, never permission to submit the modification again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from archflow.project.record_kinds import STUDIO_CANDIDATE_WORKFLOW, STUDIO_MODEL_ASSET
from archflow.project.refs import ProjectVersionRef, record_ref_from_uri, require_identifier
from archflow.project.repository import ProjectRepositoryError
from archflow.state.design_portfolio import DesignBranch

from .artifacts import ModelSource, list_artifacts, require_complete_model
from .binding import ProjectBinding, ReferenceRun, record_kind
from .candidate import _receipt
from .design_history import (
    StageView,
    _exact_runner,
    _retained_acceptance_attribution,
    read_acceptance,
)
from .jobs import FAILED, QUEUED, RUNNING, Job, JobRegistry
from ..transport.errors import StudioError


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    candidate_id: str
    status: str
    job_id: str | None = None
    proposal_id: str | None = None
    base: ProjectVersionRef | None = None
    base_record_digest: str | None = None
    base_state_digest: str | None = None
    result_record_digest: str | None = None
    result_state_digest: str | None = None
    receipt_ref: str | None = None
    commit_stage_refs: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    project_id: str
    project_dir: str
    published: ProjectVersionRef
    jobs: tuple[Job, ...]
    candidates: tuple[RuntimeCandidate, ...]
    branches: tuple[DesignBranch, ...]
    stages: tuple[StageView, ...]
    errors: tuple[str, ...]
    runs_scanned: int
    has_more: bool


def inspect_runtime(
    binding: ProjectBinding, *, jobs: JobRegistry | None = None,
    limit: int = 50, candidate_ids: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    """Inspect bounded recent runs and explicitly tracked operations without writes.

    Recent runs use the binding's stable name ordering. Explicit candidate ids
    and active jobs remain visible even when they precede that window. Jobs
    describe process execution; only retained candidate and committed branch
    readers establish the durable result.
    """

    if not 1 <= limit <= 200 or len(candidate_ids) > 200:
        raise ValueError("Runtime inspection accepts 1..200 recent runs and at most 200 explicit candidates.")
    for candidate_id in candidate_ids:
        require_identifier(candidate_id, "candidate_id")
    live = () if jobs is None else jobs.list()
    by_candidate = {job.candidate_id: job for job in live}
    # Only this snapshot shares artifact reads. Byte availability still uses
    # the artifact owner's file-identity checks on every later snapshot.
    artifacts = {}

    def artifacts_of(run_id):
        if run_id not in artifacts:
            artifacts[run_id] = list_artifacts(binding, run_id=run_id).artifacts
        return artifacts[run_id]

    errors: list[str] = []
    branches: list[DesignBranch] = []
    stages: dict[str, StageView] = {}
    branch_rows = binding.repository.read_design_branches()
    for branch_id, branch_payload in branch_rows.items():
        try:
            # Reachability proves commitment; projecting the full design and
            # recomputing action viability is not part of a status read.
            history = binding.design_history(branch_id)
            verified = {}
            for ref, stage in history:
                if ref.uri in stages:
                    continue
                record_ref, record, receipt = _exact_runner(binding, stage.candidate_id, stage.runner_ref)
                if record_ref != stage.record_ref:
                    raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The Stage record differs from its pinned runner source.")
                source = ModelSource(stage.candidate_id, receipt.get("design_state_digest"), stage.model_sha256)
                model = next((row for row in artifacts_of(stage.candidate_id) if (
                    row.receipt_ref, row.run_id, row.design_state_digest, row.sha256, row.format,
                ) == (stage.model_ref.uri, source.run_id, source.state_digest, source.asset_sha256, "3dm")), None)
                if model is None or not model.available:
                    raise StudioError(409, "DESIGN_STAGE_SOURCE_MISMATCH", "The Stage's exact retained model is unavailable.")
                require_complete_model(model, receipt)
                # The recovery view reads the same evidence under the same
                # check: the Stage's own retained attribution is what says an
                # event beside it is its acceptance.
                attribution = _retained_acceptance_attribution(binding, ref, stage)
                verified[ref.uri] = StageView(ref, stage, source, record.digest,
                                              read_acceptance(binding, ref, stage,
                                                              record_digest=record.digest,
                                                              attribution=attribution),
                                              attribution)
            stages.update(verified)
            branches.append(DesignBranch.from_dict(branch_payload))
        except (StudioError, ProjectRepositoryError, OSError, ValueError) as exc:
            errors.append(f"Branch {branch_id}: {exc}")
    run_ids = binding.run_ids()
    active_ids = tuple(job.candidate_id for job in live if job.status in (QUEUED, RUNNING))
    selected = tuple(dict.fromkeys((*candidate_ids, *active_ids, *reversed(run_ids[-limit:]))))
    tracked = set(candidate_ids) | set(by_candidate)
    candidates: list[RuntimeCandidate] = []
    for candidate_id in selected:
        job = by_candidate.get(candidate_id)
        row = RuntimeCandidate(candidate_id, "needs_recovery", job_id=job.job_id if job else None,
                               proposal_id=job.proposal_id if job else None)
        try:
            delta = binding.candidate_delta(candidate_id)
            if candidate_id not in tracked and delta is None:
                # A normal project run is not a Studio candidate. The retained
                # harness, not an id prefix, identifies older candidate runs.
                if not any(record_kind(ref) == STUDIO_CANDIDATE_WORKFLOW for ref in binding.record_refs(candidate_id)):
                    continue
            row = replace(row, base=binding.load_run(candidate_id).base)
            if delta is not None:
                operator = delta["operator"]
                row = replace(row, base_record_digest=operator["base_record_digest"],
                              base_state_digest=operator["base_state_digest"])
            if job is not None and job.status in (QUEUED, RUNNING):
                row = replace(row, status=job.status, error=job.error)
            else:
                receipt_ref, receipt = _receipt(binding, candidate_id)
                row = replace(row, result_record_digest=receipt.get("state_record_digest"),
                              result_state_digest=receipt.get("design_state_digest"), receipt_ref=receipt_ref.uri)
                if not receipt.get("seat_execution_complete"):
                    row = replace(row, status="failed", error="The retained runner receipt reports incomplete seat execution.")
                else:
                    _, record = binding.exact_state_record(ReferenceRun(
                        binding.load_run(candidate_id), "runtime", receipt,
                    ))
                    if delta is not None and delta["result_record_digest"] != record.digest:
                        raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The retained candidate change and runner result disagree.")
                    workflow_ref = receipt.get("workflow_ref")
                    if workflow_ref:
                        workflow = binding.repository.load_json(record_ref_from_uri(workflow_ref, binding.project_id))
                        composed_source = any(record_kind(record_ref_from_uri(ref, binding.project_id)) == STUDIO_MODEL_ASSET
                                              for ref in workflow.get("basis_refs", ()) if ref.startswith("project://"))
                        if composed_source and not any(
                            model.run_id == candidate_id and model.design_state_digest == row.result_state_digest
                            and model.representation == "composed" and model.available
                            for model in artifacts_of(candidate_id)
                        ):
                            raise StudioError(404, "CANDIDATE_NOT_FOUND",
                                              f"Candidate {candidate_id} has native results but no completed composed model for its retained source.")
                    row = replace(row, status="completed")
        except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
            if job is not None and job.status in (QUEUED, RUNNING, FAILED):
                row = replace(row, status=job.status, error=job.error)
            else:
                row = replace(row, error=str(exc))
        row = replace(row, commit_stage_refs=tuple(ref for ref, view in stages.items()
                                                  if view.stage.candidate_id == candidate_id))
        candidates.append(row)
    if binding.repository.read_design_branches() != branch_rows:
        raise StudioError(409, "RUNTIME_CHANGED", "Design branches changed during runtime inspection; read the next snapshot.")
    return RuntimeSnapshot(binding.project_id, str(binding.project_dir), binding.head(), live,
                           tuple(candidates), tuple(branches), tuple(stages.values()), tuple(errors),
                           len(selected), len(run_ids) > limit)
