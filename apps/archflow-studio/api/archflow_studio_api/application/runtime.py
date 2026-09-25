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
from archflow.state.state_record import StateRecord, StateRecordError, changed_refs, combine_component_changes

from .artifacts import ModelSource, list_artifacts, list_documents, require_complete_model
from .binding import ProjectBinding, ReferenceRun, record_kind
from .candidate import _receipt
from .design_history import (
    StageView,
    _exact_runner,
    _retained_acceptance_attribution,
    read_acceptance,
)
from .jobs import FAILED, QUEUED, RUNNING, Job, JobRegistry
from .working_draft import WorkingHead, lineage_of, model_is_current, read_working_draft, resolve_working_source
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
    limit: int = 50, offset: int = 0, candidate_ids: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    """Inspect bounded recent runs and explicitly tracked operations without writes.

    Recent runs use the binding's stable name ordering, newest names first.
    Offset pages that window without changing candidate classification. Explicit candidate ids
    and active jobs remain visible even when they precede that window. Jobs
    describe process execution; only retained candidate and committed branch
    readers establish the durable result.
    """

    if not 1 <= limit <= 200 or offset < 0 or len(candidate_ids) > 200:
        raise ValueError("Runtime inspection accepts 1..200 recent runs, a nonnegative offset and at most 200 explicit candidates.")
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
    recent = tuple(reversed(run_ids))[offset:offset + limit]
    selected = tuple(dict.fromkeys((*candidate_ids, *active_ids, *recent)))
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
                           len(selected), len(run_ids) > offset + limit)


# ---- Worktree Graph V0 (#271) ------------------------------------------------
#
# Who is working from which exact source, on what scope, and whether their
# lines can reconcile. Every row is derived from the retained working position,
# design branches, candidate deltas and this process's job queue; nothing here
# is stored, merged or started.

_RESULT_LIMIT = 50
_OVERLAP = "candidate changes overlap or depend on each other: "


@dataclass(frozen=True, slots=True)
class WorktreeLine:
    line_id: str
    # head | branch | running | result
    kind: str
    run_id: str | None
    job_id: str | None
    label: str | None
    base_run_id: str | None
    base_stage_ref: str | None
    branch_id: str | None
    # current | accepted | queued | running | interrupted | ready
    status: str
    # head | ahead | behind | diverged | separate
    relation: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    # none | can-combine | conflict | unknown
    reconcile: str = "none"
    conflicts: tuple[str, ...] = ()
    detail: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class RepresentationState:
    # drawing | render
    kind: str
    item_id: str
    label: str
    # current | stale | running | unavailable
    state: str
    source_run_id: str | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class WorktreeGraph:
    project_id: str
    head: WorkingHead | None
    revision_sha256: str | None
    lines: tuple[WorktreeLine, ...]
    representations: tuple[RepresentationState, ...]
    warnings: tuple[str, ...]


def _exact_record(binding: ProjectBinding, run_id: str, cache: dict[str, StateRecord]) -> StateRecord:
    if run_id not in cache:
        newest = binding.newest_runner_receipt(run_id)
        if newest is None:
            raise StudioError(409, "WORKTREE_SOURCE_UNAVAILABLE", f"Run {run_id} has no retained runner receipt.")
        cache[run_id] = binding.exact_state_record(ReferenceRun(binding.load_run(run_id), "worktree", newest[1]))[1]
    return cache[run_id]


def _protected_since(binding: ProjectBinding, run_id: str, ancestor: str) -> set[str]:
    """Keep conditions each continuation declared after the shared ancestor."""

    protected: set[str] = set()
    current = run_id
    while current != ancestor:
        delta = binding.candidate_delta(current)
        if delta is None:
            break
        protected.update(delta["operator"]["protected"])
        current = delta["source_run_ref"]["run_id"]
    return protected


def _reconcile(binding: ProjectBinding, ancestor: str, head_run: str, run_id: str,
               cache: dict[str, StateRecord]) -> tuple[tuple[str, ...], str, tuple[str, ...], str | None]:
    """The line's own writes since the shared ancestor, and whether both lines combine."""

    base = _exact_record(binding, ancestor, cache)
    record = _exact_record(binding, run_id, cache)
    writes = changed_refs(base, record)
    protected = _protected_since(binding, head_run, ancestor) | _protected_since(binding, run_id, ancestor)
    try:
        # The StateRecord owner's own combine rule, used as a dry run.
        combine_component_changes(base, (_exact_record(binding, head_run, cache), record), protected=tuple(sorted(protected)))
    except StateRecordError as exc:
        message = str(exc)
        if message.startswith(_OVERLAP):
            return writes, "conflict", tuple(message[len(_OVERLAP):].split(", ")), None
        return writes, "unknown", (), message
    return writes, "can-combine", (), None


def _relation(lineage: tuple[str, ...], head: WorkingHead | None) -> tuple[str, str | None]:
    """How a line's lineage stands to the head's: its relation and the shared ancestor."""

    if head is None:
        return "separate", None
    if lineage[0] in head.lineage:
        return "included", lineage[0]
    if head.run_id in lineage:
        return "ahead", head.run_id
    ancestor = next((run for run in lineage[1:] if run in head.lineage), None)
    return ("diverged", ancestor) if ancestor is not None else ("separate", None)


def _running_lines(binding: ProjectBinding, active: dict, jobs: JobRegistry | None,
                   head: WorkingHead | None) -> list[WorktreeLine]:
    live = {job.candidate_id: job for job in (() if jobs is None else jobs.list()) if job.status in (QUEUED, RUNNING)}
    lines: list[WorktreeLine] = []
    for run_id in sorted(set(active) | set(live)):
        job = live.get(run_id)
        sources = tuple(active.get(run_id, ()))
        base = sources[0] if len(sources) == 1 else None
        if head is None or base is None:
            relation = "separate"
        elif base == head.run_id:
            relation = "ahead"
        elif base in head.lineage:
            relation = "behind"
        else:
            relation = "diverged"
        export = job is not None and job.kind != "candidate"
        lines.append(WorktreeLine(
            line_id=f"running:{run_id}", kind="running", run_id=run_id, job_id=None if job is None else job.job_id,
            label="Model export" if export else "Design change", base_run_id=base, base_stage_ref=None,
            branch_id=None, status="interrupted" if job is None else job.status, relation=relation,
            reads=() if job is None else tuple(sorted(job.read_refs)),
            writes=() if job is None else tuple(sorted(job.write_refs)),
            detail=("The runtime stopped before this change finished; it is never replayed automatically."
                    if job is None else "Combined from several exact sources." if len(sources) > 1 else None),
            updated_at=None if job is None else job.started_at or job.created_at,
        ))
    # Two unfinished changes writing the same thing cannot both land silently.
    for index, line in enumerate(lines):
        shared = sorted({ref for other in lines if other is not line for ref in set(line.writes) & set(other.writes)})
        if shared:
            lines[index] = replace(line, reconcile="conflict", conflicts=tuple(shared))
    return lines


def _result_lines(binding: ProjectBinding, value: dict, head: WorkingHead | None,
                  warnings: list[str]) -> list[WorktreeLine]:
    """Retained working results that are not part of the head's line."""

    draft = read_working_draft(binding)
    entries = sorted([*draft.recovery, *draft.saved], key=lambda row: row.updatedAt, reverse=True)
    skip = set(value["active"]) | ({head.run_id} if head is not None else set())
    cache: dict[str, StateRecord] = {}
    lines: list[WorktreeLine] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.runId in skip or entry.runId in seen:
            continue
        seen.add(entry.runId)
        if len(lines) == _RESULT_LIMIT:
            warnings.append("More retained working results exist than this view lists.")
            break
        try:
            delta = binding.candidate_delta(entry.runId)
            if delta is None:
                continue
            lineage = lineage_of(binding, entry.runId)
            relation, ancestor = _relation(lineage, head)
            if relation == "included":
                continue
            writes, reconcile, conflicts, detail = (), "unknown", (), None
            if relation == "ahead":
                writes = changed_refs(_exact_record(binding, head.run_id, cache), _exact_record(binding, entry.runId, cache))
                reconcile, detail = "none", "This result already continues the current head."
            elif relation == "diverged":
                writes, reconcile, conflicts, detail = _reconcile(binding, ancestor, head.run_id, entry.runId, cache)
            lines.append(WorktreeLine(
                line_id=f"result:{entry.runId}", kind="result", run_id=entry.runId, job_id=None, label=entry.label,
                base_run_id=delta["source_run_ref"]["run_id"], base_stage_ref=entry.sourceStageRef,
                branch_id=entry.branchId, status="ready", relation=relation, writes=tuple(writes),
                reconcile=reconcile, conflicts=conflicts, detail=detail, updated_at=entry.updatedAt,
            ))
        except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
            warnings.append(f"Working result {entry.runId} could not be compared: {getattr(exc, 'detail', exc)}")
    return lines


def _representations(binding: ProjectBinding, head: WorkingHead | None, render_jobs) -> list[RepresentationState]:
    rows: list[RepresentationState] = []
    latest = {}
    for document in list_documents(binding):
        if (document.view_recipe or {}).get("kind") != "cut-plan" or document.model_source is None:
            continue
        key = document.drawing_id or document.file_name
        if key not in latest or (document.generated_at or "") > (latest[key].generated_at or ""):
            latest[key] = document
    for key, document in sorted(latest.items()):
        state, reason = model_is_current(binding, document.model_source.run_id, document.model_source.state_digest, head=head)
        rows.append(RepresentationState("drawing", key, key, {"current": "current", "outdated": "stale"}.get(state, "unavailable"),
                                        document.model_source.run_id, reason))
    for job in render_jobs or ():
        if job.status in ("queued", "running"):
            state = "running"
        elif job.status == "succeeded" and job.document is not None:
            state = {"current": "current", "outdated": "stale"}.get(job.source_state, "unavailable")
        else:
            continue
        label = job.document.file_name if job.document is not None else "AI Render"
        rows.append(RepresentationState("render", job.job_id, label, state, None, job.source_state_reason))
    return rows


def worktree_graph(binding: ProjectBinding, *, jobs: JobRegistry | None = None, render_jobs=None) -> WorktreeGraph:
    """Derive the project's current head, active work and other lines without writing."""

    resolved = resolve_working_source(binding)
    head, warnings = resolved.head, list(resolved.warnings)
    value, _ = binding.repository.read_working_draft()
    lines: list[WorktreeLine] = []
    if head is not None:
        lines.append(WorktreeLine(
            line_id=f"head:{head.run_id}", kind="head", run_id=head.run_id, job_id=None, label=head.label,
            base_run_id=head.lineage[1] if len(head.lineage) > 1 else None, base_stage_ref=head.source_stage_ref,
            branch_id=head.branch_id, status="current", relation="head",
            updated_at=(value["runs"].get(head.run_id) or {}).get("updatedAt"),
        ))
    for branch_id, payload in sorted(binding.repository.read_design_branches().items()):
        try:
            branch = DesignBranch.from_dict(payload)
            stage = binding.design_stage(branch.head_stage)
        except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Branch {branch_id} could not be read: {getattr(exc, 'detail', exc)}")
            continue
        if head is not None and branch_id == head.branch_id and stage.candidate_id in head.lineage:
            continue
        lines.append(WorktreeLine(
            line_id=f"branch:{branch_id}", kind="branch", run_id=stage.candidate_id, job_id=None, label=stage.label,
            base_run_id=None, base_stage_ref=branch.head_stage.uri, branch_id=branch_id, status="accepted",
            relation="separate",
        ))
    lines.extend(_running_lines(binding, value["active"], jobs, head))
    lines.extend(_result_lines(binding, value, head, warnings))
    return WorktreeGraph(binding.project_id, head, resolved.revision_sha256, tuple(lines),
                         tuple(_representations(binding, head, render_jobs)), tuple(warnings))
