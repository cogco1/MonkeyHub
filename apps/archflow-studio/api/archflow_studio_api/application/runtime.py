"""Read runtime recovery facts through the existing Studio and P036 readers.

This projection has no writer, replay, or worker-start capability. A caller
may use it after a worker exits; absence of a completed receipt is then an
interrupted operation, never permission to submit the modification again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from archflow.project.record_kinds import STUDIO_CANDIDATE_WORKFLOW
from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.project.repository import ProjectRepositoryError
from archflow.state.design_portfolio import DesignBranch

from .binding import ProjectBinding, record_kind
from .candidate import describe
from .design_history import StageView, read_design_history
from .jobs import FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobRegistry
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
    errors: list[str] = []
    branches: list[DesignBranch] = []
    stages: dict[str, StageView] = {}
    for branch_id in binding.repository.read_design_branches():
        try:
            history = read_design_history(binding, branch_id)
            branches.extend(branch for branch in history.branches if branch.branch_id == branch_id)
            stages.update((view.ref.uri, view) for view in history.stages)
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
                candidate = describe(binding, None, candidate_id=candidate_id,
                                     job_id=row.job_id, proposal_id=row.proposal_id, status=SUCCEEDED)
                row = replace(row, result_record_digest=candidate.record_digest,
                              result_state_digest=candidate.state_digest, receipt_ref=candidate.receipt_ref)
                if not candidate.seat_execution_complete:
                    row = replace(row, status="failed", error="The retained runner receipt reports incomplete seat execution.")
                elif delta is not None and delta["result_record_digest"] != candidate.record_digest:
                    row = replace(row, error="The retained candidate change and runner result disagree.")
                else:
                    # describe also refuses an unfinished composition when the
                    # harness records a composed model as its input.
                    row = replace(row, status="completed")
        except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
            if job is not None and job.status in (QUEUED, RUNNING, FAILED):
                row = replace(row, status=job.status, error=job.error)
            else:
                row = replace(row, error=str(exc))
        row = replace(row, commit_stage_refs=tuple(ref for ref, view in stages.items()
                                                  if view.stage.candidate_id == candidate_id))
        candidates.append(row)
    return RuntimeSnapshot(binding.project_id, str(binding.project_dir), binding.head(), live,
                           tuple(candidates), tuple(branches), tuple(stages.values()), tuple(errors),
                           len(selected), len(run_ids) > limit)
