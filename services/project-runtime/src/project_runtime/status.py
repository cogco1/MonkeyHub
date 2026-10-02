"""Read runtime recovery facts through the existing Studio and P036 readers.

This projection has no writer, replay, or worker-start capability. A caller
may use it after a worker exits; absence of a completed receipt is then an
interrupted operation, never permission to submit the modification again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT, INTENT_COMPILATION, STUDIO_CANDIDATE_WORKFLOW, STUDIO_MODEL_ASSET
from archflow.project.refs import ProjectVersionRef, record_ref_from_uri, require_identifier
from archflow.project.repository import ProjectRepositoryError
from archflow.state.design_portfolio import DesignBranch
from archflow.state.state_record import StateRecord, StateRecordError, changed_refs, combine_component_changes

from .application.artifacts import ModelSource, list_artifacts, require_complete_model
from .binding import ProjectBinding, ReferenceRun, RunChanges, record_kind
from .application.candidate import _receipt
from .application.design_history import (
    ADMITTED,
    AdmissionStore,
    StageView,
    _exact_runner,
    _retained_acceptance_attribution,
    admission_index,
    admission_store,
    read_acceptance,
)
from .jobs import FAILED, QUEUED, RUNNING, RUN, Job, JobRegistry
from .application.representation_dependencies import (
    CURRENT, FROZEN, OUTDATED, UNAVAILABLE, ReplacementCycle, RepresentationReads, representation_status,
)
from .application.working_draft import (
    DESIGN_CONTINUED, WorkingHead, WorkingSources, _parents, lineage_of, read_working_draft,
)
from .errors import StudioError


@dataclass(frozen=True, slots=True)
class RuntimeRun:
    """One Studio run's execution status: a run, never a Candidate (#294).

    The wire still names it ``candidates[].candidateId``, which the Hub's
    recovery and the web client read.
    """

    run_id: str
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
    runs: tuple[RuntimeRun, ...]
    branches: tuple[DesignBranch, ...]
    stages: tuple[StageView, ...]
    errors: tuple[str, ...]
    runs_scanned: int
    has_more: bool


def inspect_runtime(
    binding: ProjectBinding, *, jobs: JobRegistry | None = None,
    limit: int = 50, offset: int = 0, run_ids: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    """Inspect bounded recent runs and explicitly tracked operations without writes.

    Recent runs use the binding's stable name ordering, newest names first.
    Offset pages that window without changing run classification. Explicit run ids
    and active run jobs remain visible even when they precede that window. Jobs
    describe process execution; only retained run and committed branch
    readers establish the durable result.
    """

    if not 1 <= limit <= 200 or offset < 0 or len(run_ids) > 200:
        raise ValueError("Runtime inspection accepts 1..200 recent runs, a nonnegative offset and at most 200 explicit runs.")
    for run_id in run_ids:
        require_identifier(run_id, "run_id")
    live = () if jobs is None else jobs.list()
    # A model export runs on this queue under its export id but is not a
    # design run: its own retained report says how it ended, and it never has
    # a candidate delta or runner receipt. It is listed among the jobs only;
    # read as a run, a finished export would look like one needing recovery.
    run_jobs = tuple(job for job in live if job.kind == RUN)
    by_run = {job.candidate_id: job for job in run_jobs}
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
    retained = binding.run_ids()
    active_ids = tuple(job.candidate_id for job in run_jobs if job.status in (QUEUED, RUNNING))
    recent = tuple(reversed(retained))[offset:offset + limit]
    selected = tuple(dict.fromkeys((*run_ids, *active_ids, *recent)))
    tracked = set(run_ids) | set(by_run)
    runs: list[RuntimeRun] = []
    for run_id in selected:
        job = by_run.get(run_id)
        row = RuntimeRun(run_id, "needs_recovery", job_id=job.job_id if job else None,
                         proposal_id=job.proposal_id if job else None)
        try:
            delta = binding.candidate_delta(run_id)
            if run_id not in tracked and delta is None:
                # A normal project run is not a Studio run. The retained
                # harness, not an id prefix, identifies older Studio runs.
                if not binding.record_refs(run_id, kind=STUDIO_CANDIDATE_WORKFLOW):
                    continue
            row = replace(row, base=binding.load_run(run_id).base)
            if delta is not None:
                operator = delta["operator"]
                row = replace(row, base_record_digest=operator["base_record_digest"],
                              base_state_digest=operator["base_state_digest"])
            if job is not None and job.status in (QUEUED, RUNNING):
                row = replace(row, status=job.status, error=job.error)
            else:
                receipt_ref, receipt = _receipt(binding, run_id)
                row = replace(row, result_record_digest=receipt.get("state_record_digest"),
                              result_state_digest=receipt.get("design_state_digest"), receipt_ref=receipt_ref.uri)
                if not receipt.get("seat_execution_complete"):
                    row = replace(row, status="failed", error="The retained runner receipt reports incomplete seat execution.")
                else:
                    _, record = binding.exact_state_record(ReferenceRun(
                        binding.load_run(run_id), "runtime", receipt,
                    ))
                    if delta is not None and delta["result_record_digest"] != record.digest:
                        raise StudioError(409, "CANDIDATE_DELTA_INVALID", "The retained candidate change and runner result disagree.")
                    workflow_ref = receipt.get("workflow_ref")
                    if workflow_ref:
                        workflow = binding.repository.load_json(record_ref_from_uri(workflow_ref, binding.project_id))
                        composed_source = any(record_kind(record_ref_from_uri(ref, binding.project_id)) == STUDIO_MODEL_ASSET
                                              for ref in workflow.get("basis_refs", ()) if ref.startswith("project://"))
                        if composed_source and not any(
                            model.run_id == run_id and model.design_state_digest == row.result_state_digest
                            and model.representation == "composed" and model.available
                            for model in artifacts_of(run_id)
                        ):
                            raise StudioError(404, "CANDIDATE_NOT_FOUND",
                                              f"Candidate {run_id} has native results but no completed composed model for its retained source.")
                    row = replace(row, status="completed")
        except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
            if job is not None and job.status in (QUEUED, RUNNING, FAILED):
                row = replace(row, status=job.status, error=job.error)
            else:
                row = replace(row, error=str(exc))
        row = replace(row, commit_stage_refs=tuple(ref for ref, view in stages.items()
                                                  if view.stage.candidate_id == run_id))
        runs.append(row)
    if binding.repository.read_design_branches() != branch_rows:
        raise StudioError(409, "RUNTIME_CHANGED", "Design branches changed during runtime inspection; read the next snapshot.")
    return RuntimeSnapshot(binding.project_id, str(binding.project_dir), binding.head(), live,
                           tuple(runs), tuple(branches), tuple(stages.values()), tuple(errors),
                           len(selected), len(retained) > offset + limit)


# ---- Worktree Graph V0 (#271) ------------------------------------------------
#
# Who is working from which exact source, on what scope, and whether their
# lines can reconcile. Every row is derived from the retained working position,
# design branches, candidate deltas and this process's job queue; nothing here
# is stored, merged or started.

_RESULT_LIMIT = 50
_OVERLAP = "candidate changes overlap or depend on each other: "
# As far as a lineage is walked (working_draft's limit).
_LINE_LIMIT = 64
_UNREADABLE = (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError)


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
    # head | ahead | behind | diverged | superseded | separate
    relation: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    # none | can-combine | conflict | unknown
    reconcile: str = "none"
    conflicts: tuple[str, ...] = ()
    detail: str | None = None
    updated_at: str | None = None
    # admitted | rejected | superseded | none, from retained admission facts (#294)
    admission: str = "none"
    study_id: str | None = None
    # For a superseded draft: the step of the head's line that replaced it (#575).
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class LineStep:
    """One run of the Working Head's line, in the words its retained facts give it (#575).

    The line is the head's first-parent chain: each step names the run it was
    made from, so the inputs a combine merged stay off it. ``label`` is the
    name a person gave the run (a saved version's label), else its accepted
    Stage's, else its admitted result's; ``request`` is the words that asked
    for it, when they were retained (its admission's ``rawLanguage``, else the
    request its own change keeps, else the sentence an intent model compiled
    into it); ``summary`` is an admitted result's own account of the change.
    The steps the head moved back past (``WorktreeGraph.later``) are named the
    same way.
    """

    run_id: str
    base_run_id: str | None
    label: str | None = None
    request: str | None = None
    summary: str | None = None
    stage_ref: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class RepresentationState:
    # drawing | render
    kind: str
    item_id: str
    label: str
    # current | stale | frozen | running | unavailable
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
    # The head's line, oldest first and ending at the head (#575).
    line: tuple[LineStep, ...] = ()
    # After a return to an earlier step, the line the head left: oldest first, from the step made from the head (#575).
    later: tuple[LineStep, ...] = ()


class _GraphReads:
    """What one Worktree Graph reads more than once, read once for the whole graph (#575).

    A graph compares many lines with the head's, and each comparison walks
    the same runs again. ``changes`` answers each run's retained change,
    ``parents`` is the one parent map every walk of the graph shares
    (``lineage_of``'s ``known``), and ``records`` holds each run's exact State
    Record. Nothing read here answers the next graph.
    """

    __slots__ = ("binding", "changes", "parents", "records")

    def __init__(self, binding: ProjectBinding) -> None:
        self.binding = binding
        self.changes = RunChanges(binding)
        self.parents: dict[str, tuple[str, ...]] = {}
        self.records: dict[str, StateRecord] = {}

    def parents_of(self, run_id: str) -> tuple[str, ...]:
        """The runs one run continued, read into the graph's map the way ``lineage_of`` reads them."""

        if run_id not in self.parents:
            try:
                self.parents[run_id] = _parents(self.binding, run_id)
            except _UNREADABLE:
                self.parents[run_id] = ()
        return self.parents[run_id]

    def lineage(self, run_id: str) -> tuple[str, ...]:
        """The run and the runs it continued or combined, nearest first, walked over the graph's map."""

        return lineage_of(self.binding, run_id, known=self.parents)

    def record(self, run_id: str) -> StateRecord:
        """The State Record the run's newest runner receipt names, checked exactly."""

        return _exact_record(self.binding, run_id, self.records)


def _exact_record(binding: ProjectBinding, run_id: str, cache: dict[str, StateRecord]) -> StateRecord:
    if run_id not in cache:
        newest = binding.newest_runner_receipt(run_id)
        if newest is None:
            raise StudioError(409, "WORKTREE_SOURCE_UNAVAILABLE", f"Run {run_id} has no retained runner receipt.")
        cache[run_id] = binding.exact_state_record(ReferenceRun(binding.load_run(run_id), "worktree", newest[1]))[1]
    return cache[run_id]


def _protected_since(changes: RunChanges, run_id: str, ancestor: str) -> set[str]:
    """Keep conditions each continuation declared after the shared ancestor."""

    protected: set[str] = set()
    current = run_id
    while current != ancestor:
        delta = changes(current)
        if delta is None:
            break
        protected.update(delta["operator"]["protected"])
        current = delta["source_run_ref"]["run_id"]
    return protected


def _reconcile(reads: _GraphReads, ancestor: str, head_run: str,
               run_id: str) -> tuple[tuple[str, ...], str, tuple[str, ...], str | None]:
    """The line's own writes since the shared ancestor, and whether both lines combine."""

    base = reads.record(ancestor)
    record = reads.record(run_id)
    writes = changed_refs(base, record)
    protected = _protected_since(reads.changes, head_run, ancestor) | _protected_since(reads.changes, run_id, ancestor)
    try:
        # The StateRecord owner's own combine rule, used as a dry run.
        combine_component_changes(base, (reads.record(head_run), record), protected=tuple(sorted(protected)))
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


# ---- The head's line and the drafts it left behind (#575) ---------------------
#
# The person sees one line: the runs the Working Head was made through, each
# named by what its retained facts say about it. A retained result built where
# the line later moved on through another step, which changes what the line
# changed since (its comparison with the head conflicts), which nobody
# continued or admitted, and on which nothing that somebody continued or
# admitted was built, is a draft that step superseded. It is listed as
# superseded rather than as a diverged line; nothing about it is moved or
# deleted.

# Retained records never change, so each run's request is read once per process.
_REQUESTS: dict[tuple[str, str], str | None] = {}
# Continue events are never removed: a run once continued stays continued.
_CONTINUED: set[tuple[str, str]] = set()
_MEMO_LIMIT = 4096


def _compiled_request(binding: ProjectBinding, run_id: str, changes: RunChanges | None = None) -> str | None:
    """The words this run was asked in, as the run itself retained them (#575).

    The request its change keeps (``StudioCandidateDelta@1`` ``request``): a
    sentence, or an outside agent's summary. A run made before changes kept
    one was asked in the sentence an intent model compiled into it, when a
    model did. None for a change nobody asked for in words. ``changes`` is
    the caller's own reading of the runs' changes, when it keeps one.
    """

    key = (str(binding.repository.layout.root), run_id)
    if key in _REQUESTS:
        return _REQUESTS[key]
    try:
        kept = ((binding.candidate_delta if changes is None else changes)(run_id) or {}).get("request")
        if isinstance(kept, str) and kept.strip():
            words = kept.strip()
        else:
            payloads = [binding.repository.load_json(ref) for ref in binding.record_refs(run_id, kind=INTENT_COMPILATION)]
            words = next((payload["utterance"].strip() for payload in payloads
                          if isinstance(payload.get("utterance"), str) and payload["utterance"].strip()), None)
    except _UNREADABLE:
        return None
    if len(_REQUESTS) >= _MEMO_LIMIT:
        _REQUESTS.clear()
    _REQUESTS[key] = words
    return words


def _admitted_words(store: AdmissionStore, run_id: str) -> tuple[str | None, str | None, str | None]:
    """An admitted result's label and summary, and the words its loop was asked in."""

    claims = store.claims.get(run_id, ())
    # A run that live records compete for is left out of every read.
    if len(claims) != 1 or claims[0].superseded or claims[0].outcome != ADMITTED:
        return None, None, None
    row: Mapping[str, Any] = next((row for row in claims[0].record.results if row["runId"] == run_id), {})
    return row.get("label"), row.get("summary"), claims[0].record.payload.get("rawLanguage")


@dataclass(frozen=True, slots=True)
class _LineWords:
    """What a line's runs are named by, read once for the whole graph: each Stage's run and label, and the admissions."""

    stages: Mapping[str, str]
    labels: Mapping[str, str]
    store: AdmissionStore


def _line_words(binding: ProjectBinding, warnings: list[str]) -> _LineWords:
    stages: dict[str, str] = {}
    labels: dict[str, str] = {}
    for branch_id in sorted(binding.repository.read_design_branches()):
        try:
            for ref, stage in binding.design_history(branch_id):
                if stage.candidate_id not in stages:
                    stages[stage.candidate_id], labels[stage.candidate_id] = ref.uri, stage.label
        except _UNREADABLE as exc:
            warnings.append(f"Branch {branch_id} could not be read for the line: {getattr(exc, 'detail', exc)}")
    return _LineWords(stages, labels, admission_store(binding))


def _step(reads: _GraphReads, run_id: str, base_run_id: str | None, value: dict, words: _LineWords) -> LineStep:
    """One run of a line, in the words its retained facts give it."""

    entry = value["runs"].get(run_id) or {}
    label, summary, said = _admitted_words(words.store, run_id)
    return LineStep(
        run_id=run_id, base_run_id=base_run_id,
        label=entry.get("label") or words.labels.get(run_id) or label,
        request=said or _compiled_request(reads.binding, run_id, reads.changes), summary=summary,
        stage_ref=words.stages.get(run_id), updated_at=entry.get("updatedAt"),
    )


def _head_line(reads: _GraphReads, head: WorkingHead | None, value: dict,
               words: _LineWords | None) -> tuple[LineStep, ...]:
    """The head's first-parent chain, oldest first, each step in its retained words."""

    if head is None or words is None:
        return ()
    runs = [head.run_id]
    bases: dict[str, str | None] = {}
    for run_id in runs:
        parents = reads.parents_of(run_id)
        bases[run_id] = parents[0] if parents else None
        if parents and parents[0] not in runs and len(runs) < _LINE_LIMIT:
            runs.append(parents[0])
    return tuple(_step(reads, run_id, bases[run_id], value, words) for run_id in reversed(runs))


def _later_line(reads: _GraphReads, head: WorkingHead | None, value: dict, results: list[WorktreeLine],
                words: _LineWords | None, warnings: list[str]) -> tuple[LineStep, ...]:
    """The steps the head moved back past (#575): after a return to an earlier step, the line it left.

    Of the retained results that continue the head, the ones the head once
    stood on (a Continue event names them) and the runs each was made through
    from the head, by first parents, are the line it left. Where that line
    forked, it goes on along the branch the working position moved onto most
    recently, to the last run there the head stood on. Oldest first, from the
    step made from the head; each can be continued again. Nothing is written.
    """

    if head is None or words is None:
        return ()
    paths: list[list[str]] = []
    for line in results:
        if line.kind != "result" or line.relation != "ahead" or line.run_id is None:
            continue
        if not _was_continued(reads.binding, line.run_id, warnings):
            continue
        path: list[str] | None = [line.run_id]
        while path is not None:
            parents = reads.parents_of(path[-1])
            if parents and parents[0] == head.run_id:
                break
            # Not made from the head by first parents (a combine's input), or past the walk's limit.
            path = None if not parents or parents[0] in path or len(path) >= _LINE_LIMIT else [*path, parents[0]]
        if path is not None:
            paths.append(path[::-1])

    def moved_onto(run_id: str) -> str:
        return (value["runs"].get(run_id) or {}).get("updatedAt") or ""

    later: list[str] = []
    while True:
        depth = len(later)
        newest: dict[str, str] = {}
        for path in paths:
            if len(path) > depth and path[:depth] == later:
                newest[path[depth]] = max(newest.get(path[depth], ""), moved_onto(path[-1]))
        if not newest:
            break
        later.append(max(newest, key=lambda run_id: (newest[run_id], run_id)))
    return tuple(_step(reads, run_id, base, value, words) for run_id, base in zip(later, (head.run_id, *later)))


def _was_continued(binding: ProjectBinding, run_id: str, warnings: list[str]) -> bool:
    """Whether the Working Head ever stood on this run, as the Continue events beside it say.

    A run whose events cannot be read counts as continued: it is compared as
    a diverged line, as it was before anything was called superseded.
    """

    key = (str(binding.repository.layout.root), run_id)
    if key in _CONTINUED:
        return True
    try:
        refs = binding.repository.list_json(
            run=binding.load_run(run_id), record_kind=AUDIT_EVENT,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id))
        continued = any(payload.get("action") == DESIGN_CONTINUED and payload.get("targetRunId") == run_id
                        for payload in map(binding.repository.load_json, refs))
    except _UNREADABLE as exc:
        warnings.append(f"Working result {run_id}'s Continue events could not be read: {getattr(exc, 'detail', exc)}")
        return True
    if continued:
        if len(_CONTINUED) >= _MEMO_LIMIT:
            _CONTINUED.clear()
        _CONTINUED.add(key)
    return continued


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
        export = job is not None and job.kind != RUN
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


def _result_lines(reads: _GraphReads, value: dict, head: WorkingHead | None, line: tuple[LineStep, ...],
                  admissions: Mapping[str, tuple[str, str | None]], warnings: list[str]) -> list[WorktreeLine]:
    """Retained working results that are not part of the head's line.

    A diverged result that changes what the head's line changed after it
    moved on from the result's source, and that nobody took further, is a
    draft that line's step superseded (#575). It keeps the comparison that
    says so, for whoever asks; clients fold it rather than show its conflicts.
    """

    binding = reads.binding
    draft = read_working_draft(binding)
    entries = sorted([*draft.recovery, *draft.saved], key=lambda row: row.updatedAt, reverse=True)
    skip = set(value["active"]) | ({head.run_id} if head is not None else set())
    found = []
    seen: set[str] = set()
    for entry in entries:
        if entry.runId in skip or entry.runId in seen:
            continue
        seen.add(entry.runId)
        # A result the head already contains is its history, not another line.
        if head is not None and entry.runId in head.lineage:
            continue
        if len(found) == _RESULT_LIMIT:
            warnings.append("More retained working results exist than this view lists.")
            break
        try:
            delta = reads.changes(entry.runId)
            if delta is None:
                continue
            lineage = reads.lineage(entry.runId)
            relation, ancestor = _relation(lineage, head)
            if relation != "included":
                found.append((entry, delta, lineage, relation, ancestor))
        except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
            warnings.append(f"Working result {entry.runId} could not be compared: {getattr(exc, 'detail', exc)}")
    # Where the line moved on: each of its runs, and the step made from it.
    moved_on = {step.base_run_id: step.run_id for step in line if step.base_run_id is not None}
    # A result somebody took further keeps its whole line: what was continued or admitted, and all it was built on.
    kept: set[str] = set()
    for entry, _delta, lineage, relation, _ancestor in found:
        if relation == "diverged" and ((admissions.get(entry.runId) or ("none",))[0] == ADMITTED
                                       or _was_continued(binding, entry.runId, warnings)):
            kept.update(lineage)
    if any(relation == "diverged" and ancestor in moved_on and entry.runId not in kept
           for entry, _delta, _lineage, relation, ancestor in found):
        # An admitted Candidate this view does not list (a Stage's run, an older result) keeps its line too.
        listed = {entry.runId for entry, *_rest in found}
        for run_id, (outcome, _study) in admissions.items():
            if outcome == ADMITTED and run_id not in listed and run_id not in kept and run_id not in head.lineage:
                kept.update(reads.lineage(run_id))
    lines: list[WorktreeLine] = []
    for entry, delta, lineage, relation, ancestor in found:
        try:
            writes, reconcile, conflicts, detail, superseded_by = (), "unknown", (), None, None
            if relation == "ahead":
                writes = changed_refs(reads.record(head.run_id), reads.record(entry.runId))
                reconcile, detail = "none", "This result already continues the current head."
            elif relation == "diverged":
                writes, reconcile, conflicts, detail = _reconcile(reads, ancestor, head.run_id, entry.runId)
                # Another attempt at what the line went on to change, which nobody took further. Work that
                # combines with the head replaced nothing and stays a diverged line. The comparison stays.
                if reconcile == "conflict" and ancestor in moved_on and entry.runId not in kept:
                    relation, superseded_by = "superseded", moved_on[ancestor]
                    detail = (f"Built from {ancestor}, where the line moved on through {superseded_by} and changed what "
                              "it changes; nobody continued or admitted it, or anything built on it.")
            lines.append(WorktreeLine(
                line_id=f"result:{entry.runId}", kind="result", run_id=entry.runId, job_id=None, label=entry.label,
                base_run_id=delta["source_run_ref"]["run_id"], base_stage_ref=entry.sourceStageRef,
                branch_id=entry.branchId, status="ready", relation=relation, writes=tuple(writes),
                reconcile=reconcile, conflicts=conflicts, detail=detail, updated_at=entry.updatedAt,
                superseded_by=superseded_by,
            ))
        except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
            warnings.append(f"Working result {entry.runId} could not be compared: {getattr(exc, 'detail', exc)}")
    return lines


def _admissions(binding: ProjectBinding, warnings: list[str]) -> dict[str, tuple[str, str | None]]:
    """Each run's retained admission and Study (#294), read once for the whole graph."""

    try:
        index, found = admission_index(binding)
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
        warnings.append(f"Candidate admissions could not be read: {getattr(exc, 'detail', exc)}")
        return {}
    warnings.extend(warning for warning in found if warning not in warnings)
    return index


def _with_admissions(lines: list[WorktreeLine], index: Mapping[str, tuple[str, str | None]]) -> list[WorktreeLine]:
    """Each finished line's retained verdict and Study; running work has none yet."""

    return [
        replace(line, admission=index[line.run_id][0], study_id=index[line.run_id][1])
        if line.kind != "running" and line.run_id in index else line
        for line in lines
    ]


# The graph's words for the representation-status vocabulary.
_GRAPH_STATE = {CURRENT: "current", OUTDATED: "stale", FROZEN: "frozen", UNAVAILABLE: "unavailable"}


def _representations(binding: ProjectBinding, render_jobs, warnings: list[str],
                     working: WorkingSources) -> list[RepresentationState]:
    """Each drawing's latest revision and each render attempt, in one status vocabulary.

    A drawing row is the representation-status projection of its latest page,
    so it says what the Drawing tool says: a change outside the plan's read set
    leaves it current. A render row is the render owner's reading of the
    attempt's retained request, the reader that projection uses for an AI page.
    """

    rows: list[RepresentationState] = []
    # The graph's own Working Head and one document listing for every row.
    reads = RepresentationReads(binding, working)
    latest = {}
    try:
        documents = reads.documents
    except (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError) as exc:
        warnings.append(f"Drawings could not be read: {getattr(exc, 'detail', exc)}")
        documents = ()
    for document in documents:
        if (document.view_recipe or {}).get("kind") != "cut-plan" or document.model_source is None:
            continue
        key = document.drawing_id or document.file_name
        if key not in latest or (document.generated_at or "") > (latest[key].generated_at or ""):
            latest[key] = document
    for key, document in sorted(latest.items()):
        try:
            status = representation_status(
                binding, (document.run_id, document.asset_sha256, document.revision_ref, 0), reads=reads)
            state, detail = status.state, status.reason
        except ReplacementCycle as exc:
            state, detail = UNAVAILABLE, str(exc)
        rows.append(RepresentationState("drawing", key, key, _GRAPH_STATE[state], document.model_source.run_id, detail))
    for job in render_jobs or ():
        if job.status in ("queued", "running"):
            state = "running"
        elif job.status == "succeeded" and job.document is not None:
            state = _GRAPH_STATE.get(job.source_state, "unavailable")
        else:
            continue
        label = job.document.file_name if job.document is not None else "AI Render"
        rows.append(RepresentationState("render", job.job_id, label, state, None, job.source_state_reason))
    return rows


def worktree_graph(binding: ProjectBinding, *, jobs: JobRegistry | None = None, render_jobs=None) -> WorktreeGraph:
    """Derive the project's current head, its line, active work and other lines without writing.

    One build reads each run's retained change through one ``RunChanges``,
    walks every lineage over one parent map and keeps each exact State Record
    once (``_GraphReads``), for all its lines and steps. It resolves the
    Working Head once, for itself and for its representation rows.
    """

    working = WorkingSources(binding)
    resolved = working()
    head, warnings = resolved.head, list(resolved.warnings)
    value, _ = binding.repository.read_working_draft()
    reads = _GraphReads(binding)
    admissions = _admissions(binding, warnings)
    words = None if head is None else _line_words(binding, warnings)
    line = _head_line(reads, head, value, words)
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
    results = _result_lines(reads, value, head, line, admissions, warnings)
    lines.extend(results)
    later = _later_line(reads, head, value, results, words, warnings)
    lines = _with_admissions(lines, admissions)
    representations = _representations(binding, render_jobs, warnings, working)
    return WorktreeGraph(binding.project_id, head, resolved.revision_sha256, tuple(lines),
                         tuple(representations), tuple(warnings), line, later)
