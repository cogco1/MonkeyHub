"""What the line moved past goes to the project trash, restorable for 30 days (#575).

Kaiwen's rule (2026-10-01), like autosave: a project keeps the steps one can
return to, not every version made on the way. Only these may be cleaned:

- a draft the head's line superseded: the Worktree Graph's ``superseded``
  (#581), built where the line later moved on and changing what it changed;
- an attempt an admitted result replaced (its admission's ``supersedes``), or
  a result whose own loop withdrew it, with the attempts it replaced;
- a failed attempt: a design change whose run never finished.

and only when all of these hold:

- it is on no kept line: not the Working Head's, no design branch Stage's and
  no admitted result's;
- nobody continued, admitted or rejected it, and nothing built on it stays;
- nothing refers to it: no retained record outside its own run names it but
  the admission that replaced or withdrew it, and its own run holds only what
  its execution wrote - no drawing page, render, annotation, Board scene,
  working copy, review, Stage or attributed act;
- it is no version a person saved, no working position a person chose, and
  no execution's input. A name keeps a draft only when the person saved it in
  the Hub (the history panel's save, which says so); a name an agent or any
  other caller saved, and any name on a row written before rows recorded who
  saved it, keeps nothing: the trash keeps that name with the draft.

When unsure, nothing goes: an unreadable working position, admission or design
branch stops the whole sweep, and a run whose reading fails stays. The trash is
the repository's (``archflow.project``: ``trash_run``): a run moves whole with
its manifest and working row, can be restored for 30 days, and is then purged.
Each sweep that moved runs retains one ``design.cleaned`` audit event, and each
restore one ``design.restored``, in the fixed ``studio-retention`` run. Those
events name the runs, so a run once restored is never cleaned again.

``RetentionSweeps`` runs it on a thread of its own: at project open, once the
project index has loaded, after purging expired entries; and after each
Continue. Nothing here moves the Working Head, a design branch or HEAD.

Every sweep also expires superseded local recovery (``prune_recovery``), and so
does the Runtime's own 15-minute cadence while the project is open: Modeling's
crash-recovery snapshots that are neither the current ``localDraftRef`` nor
updated within 24 hours, by the repository's rule (``prune_working_draft``),
never a run. The Hub did this on its own timer, from its own process, until
ADR-012 made the open project's Runtime its only writer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Callable
from uuid import uuid4

from archflow.project.ports import PersistenceArea, PersistenceDestination, TrashEntry
from archflow.project.record_kinds import (
    AUDIT_EVENT,
    BLENDER_PROJECTION,
    CANDIDATE_ADMISSION,
    DELIBERATION_EPISODE,
    DEVELOPED_DESIGN_STATE,
    DISCIPLINE_SEAT,
    GEOMETRY_PROGRAM_PROPOSAL,
    GEOMETRY_PROPOSAL_COMPLETION,
    GEOMETRY_PROPOSAL_DEFERRAL,
    GEOMETRY_PROPOSAL_ESCALATION,
    GEOMETRY_PROPOSAL_LINEAGE,
    GEOMETRY_PROPOSAL_ROUND,
    INTENT_COMPILATION,
    PROJECT_GRIDS,
    PROJECT_LEVELS,
    RECORD_KINDS,
    RUNNER_RUN_FAILURE,
    RUNNER_RUN_RECEIPT,
    SEAT_3DM_INSPECTION,
    SEAT_AUTHORING_CONTEXT,
    SEAT_BLENDER_EXECUTION,
    SEAT_GEOMETRY_PROGRAM,
    SEAT_HANDOVER,
    SEAT_OCCT_EXECUTION,
    SEAT_RELATION_CHECK,
    SEAT_RHINO_EXECUTION,
    SEAT_ROUND_RECEIPT,
    SELECTED_SPATIAL_OPTION,
    STAGE_CLOSURE,
    STAGE_EXIT_BINDING,
    STAGE_GEOMETRY_PROGRAM,
    STATE_RECORD,
    STUDIO_CANDIDATE_DELTA,
    STUDIO_CANDIDATE_ENVELOPE,
    STUDIO_CANDIDATE_WORKFLOW,
    STUDIO_MODEL_ASSET,
    require_registered,
)
from archflow.project.refs import require_identifier
from archflow.project.repository import (
    TRASH_RETENTION,
    ProjectRepositoryError,
    RunNotRestored,
    RunNotTrashed,
    TrashEntryNotFound,
    names_run,
    saved_by_person,
)

from ..binding import ProjectBinding, bound_project, record_kind
from ..errors import StudioError
from ..jobs import JobRegistry
from ..status import _compiled_request, worktree_graph
from .design_history import ADMISSIONS_RUN_ID, ADMITTED, REJECTED, WITHDRAWN, AdmissionStore, admission_store
from .working_draft import lineage_of

_LOG = logging.getLogger(__name__)

RETENTION_DAYS = TRASH_RETENTION.days
# How often an open project's superseded local recovery is expired besides each sweep: the Hub's old timer.
RECOVERY_PRUNE_INTERVAL_S = 15 * 60
# A sweep asked for while one runs is folded into one more: an open also purges and cleans, a Continue
# cleans, and every sweep expires recovery, so the broader request stands for the narrower.
_BREADTH = {"prune": 0, "continue": 1, "open": 2}
RULE_SUPERSEDED = "superseded"
RULE_REPLACED = "replaced-attempt"
RULE_FAILED = "failed-attempt"
# The fixed run that keeps who cleaned and who restored what (``design.cleaned``, ``design.restored``).
RETENTION_RUN_ID = "studio-retention"
DESIGN_CLEANED = "design.cleaned"
DESIGN_RESTORED = "design.restored"
# Cleaning is the runtime's own act under the owner's retention rule; no person asked for it.
RETENTION_ACTOR_ID = "studio:retention-rule"
ORIGIN_RUNTIME = "runtime"
_AUDIT_EVENT_SCHEMA = RECORD_KINDS[AUDIT_EVENT].schema
# The working position names every run it lists; the repository moves a draft's row with it.
_WORKING_FILE = "design/working.json"
_UNREADABLE = (StudioError, ProjectRepositoryError, KeyError, TypeError, ValueError, OSError)

# What a design change's own execution retains in its run: its change and its sources' words, the runner's
# records (its harness stage's closure and exit binding, a stage's geometry program and a proposal's rounds
# by their table entries), and its registered model. A run that holds any other kind keeps something made
# from it or about it - a drawing page, a render, an annotation, a Board scene, a working copy, an export -
# and stays; so does a kind newer than this list, and a project stage's own envelope.
_EXECUTION_KINDS = frozenset({
    STUDIO_CANDIDATE_DELTA, STUDIO_CANDIDATE_WORKFLOW, STUDIO_CANDIDATE_ENVELOPE, INTENT_COMPILATION,
    DELIBERATION_EPISODE, STATE_RECORD, PROJECT_LEVELS, PROJECT_GRIDS, SELECTED_SPATIAL_OPTION,
    DEVELOPED_DESIGN_STATE, DISCIPLINE_SEAT, SEAT_AUTHORING_CONTEXT, SEAT_GEOMETRY_PROGRAM, SEAT_RELATION_CHECK,
    SEAT_ROUND_RECEIPT, SEAT_HANDOVER, SEAT_RHINO_EXECUTION, SEAT_OCCT_EXECUTION, SEAT_BLENDER_EXECUTION,
    BLENDER_PROJECTION, SEAT_3DM_INSPECTION, RUNNER_RUN_FAILURE, RUNNER_RUN_RECEIPT, STAGE_GEOMETRY_PROGRAM,
    GEOMETRY_PROPOSAL_ROUND, GEOMETRY_PROPOSAL_COMPLETION, GEOMETRY_PROGRAM_PROPOSAL, GEOMETRY_PROPOSAL_DEFERRAL,
    GEOMETRY_PROPOSAL_ESCALATION, GEOMETRY_PROPOSAL_LINEAGE, STUDIO_MODEL_ASSET, STAGE_CLOSURE, STAGE_EXIT_BINDING,
})


@dataclass(frozen=True, slots=True)
class Cleanable:
    """One run a sweep may move, and what the trash will say about it."""

    run_id: str
    rule: str
    reason: str
    superseded_by: str | None = None
    base_run_id: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CleaningPlan:
    """What may go now, in the order it can go, and why every other run that was considered stays."""

    runs: tuple[Cleanable, ...]
    # Each run's runs among ``runs`` that name it: they go first.
    named_by: Mapping[str, frozenset[str]]
    kept: Mapping[str, str]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Sweep:
    """One automatic cleaning: what moved, what was purged, which recovery expired, and why the rest stayed."""

    trigger: str
    cleaned: tuple[TrashEntry, ...]
    purged: tuple[str, ...]
    kept: Mapping[str, str]
    warnings: tuple[str, ...]
    # The superseded local recovery snapshots this sweep expired, project-relative.
    expired: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrashView:
    """The project trash as a person reads it: each entry and when it is purged."""

    project_id: str
    retention_days: int
    entries: tuple[TrashEntry, ...]

    def expires_at(self, entry: TrashEntry) -> str:
        return (datetime.fromisoformat(entry.trashed_at) + TRASH_RETENTION).isoformat()


def _detail(exc: BaseException) -> str:
    return exc.detail if isinstance(exc, StudioError) else str(exc) or type(exc).__name__


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("a retention time must include its timezone")
    return moment.isoformat()


# ---- which runs may go -------------------------------------------------------------------------------


def _admission_mentions_explained(binding: ProjectBinding, run_id: str) -> bool:
    """Whether every record in the admissions run that names ``run_id`` names it only as replaced or withdrawn.

    An admission's ``supersedes`` under an admitted or withdrawn result names an attempt that result
    replaced, and a withdrawn result's own row names that result: those are why it may go. Any other
    mention - an admitted or rejected result, a Study based on it, another record - keeps it.
    """

    run = binding.load_run(ADMISSIONS_RUN_ID)
    for area in (PersistenceArea.RUN_REVIEW, PersistenceArea.RUN_RECORD):
        for ref in binding.repository.list_json(run=run, destination=PersistenceDestination(area, run_id=run.run_id)):
            payload = binding.repository.load_json(ref)
            for path in _mentions(payload, run_id):
                if record_kind(ref) != CANDIDATE_ADMISSION or not _explained(payload, path, run_id):
                    return False
    return True


def _mentions(value: Any, run_id: str, path: tuple = ()) -> Iterable[tuple]:
    """Where in one JSON value ``run_id`` is named whole, by path."""

    if isinstance(value, str):
        if names_run(value.encode("utf-8", "surrogatepass"), run_id):
            yield path
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _mentions(item, run_id, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _mentions(item, run_id, (*path, index))


def _explained(payload: Mapping[str, Any], path: tuple, run_id: str) -> bool:
    if len(path) < 3 or path[0] != "results" or not isinstance(path[1], int):
        return False
    row = payload["results"][path[1]]
    if path[2] == "supersedes" and row.get("outcome") in (ADMITTED, WITHDRAWN):
        return True
    return row.get("runId") == run_id and row.get("outcome") == WITHDRAWN


def _own_records_hold(binding: ProjectBinding, run_id: str) -> str | None:
    """What in the run's own directory keeps it, or None when it holds only what its execution wrote."""

    run = binding.load_run(run_id)
    layout = binding.repository.layout.run(run_id)
    for area, words in ((PersistenceArea.RUN_REVIEW, "it keeps a review, Stage or attributed act (such as a Continue)"),
                        (PersistenceArea.RUN_RECOVERY, "it keeps local recovery")):
        if binding.repository.list_json(run=run, destination=PersistenceDestination(area, run_id=run_id)):
            return words
    destinations = [PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)]
    if layout.branches.is_dir():
        destinations += [PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run_id, branch_id=branch.name)
                         for branch in sorted(layout.branches.iterdir()) if branch.is_dir()]
    for destination in destinations:
        for ref in binding.repository.list_json(run=run, destination=destination):
            kind = record_kind(ref)
            try:
                entry = None if kind is None else require_registered(kind).kind
            except ValueError:
                entry = None
            if entry not in _EXECUTION_KINDS:
                return f"it keeps {kind or 'an unnamed record'}, which was made from it or about it"
    return None


def _held(binding: ProjectBinding, run_id: str, working: Mapping[str, Any], store: AdmissionStore,
          kept_lines: set[str]) -> str | None:
    """Why one considered run stays, before anything outside it is read; None when it may go so far."""

    if run_id in kept_lines:
        return "it is on a kept line: the Working Head's, a Stage's or an admitted result's"
    claims = store.claims.get(run_id, ())
    if len(claims) > 1:
        return "admissions compete for it"
    if claims and not claims[0].superseded and claims[0].outcome == ADMITTED:
        return "it was admitted"
    if claims and not claims[0].superseded and claims[0].outcome == REJECTED:
        return "a person rejected it, and the rejection keeps it"
    if working["current"] == run_id:
        return "it is the Working Head"
    if run_id in working["active"] or any(run_id in sources for sources in working["active"].values()):
        return "an execution is using it"
    row = working["runs"].get(run_id)
    if row is not None and saved_by_person(row):
        return "a person saved it as a version"
    if row is not None and not row["automatic"]:
        return "a person chose it as the working position"
    if binding.candidate_delta(run_id) is None:
        return "it is no design change"
    return _own_records_hold(binding, run_id)


def _considered(binding: ProjectBinding, graph, working: Mapping[str, Any], store: AdmissionStore,
                jobs: JobRegistry | None) -> dict[str, Cleanable]:
    """Every run one of the three rules names, before any condition is checked."""

    found: dict[str, Cleanable] = {}
    present = set(binding.run_ids())
    for line in graph.lines:
        if line.kind == "result" and line.relation == "superseded" and line.run_id and line.superseded_by:
            found[line.run_id] = Cleanable(
                line.run_id, RULE_SUPERSEDED,
                f"Built from {line.base_run_id}, where the line moved on through {line.superseded_by} and changed what "
                "it changes; nobody continued or admitted it.",
                superseded_by=line.superseded_by, base_run_id=line.base_run_id,
                label=line.label or _compiled_request(binding, line.run_id))
    for run_id, claims in sorted(store.claims.items()):
        if run_id in found or run_id not in present or len(claims) != 1:
            continue
        claim = claims[0]
        if claim.superseded and claim.outcome in (ADMITTED, WITHDRAWN):
            reason = (f"Admission {claim.record.admission_id} replaced this attempt with {claim.result_run_id}."
                      if claim.outcome == ADMITTED else
                      f"Its loop withdrew {claim.result_run_id}, which replaced this attempt "
                      f"(admission {claim.record.admission_id}).")
            superseded_by = claim.result_run_id
        elif not claim.superseded and claim.outcome == WITHDRAWN:
            reason, superseded_by = f"Its loop withdrew it (admission {claim.record.admission_id}).", None
        else:
            continue
        # A name no person saved goes with it, so the trash can say what it was.
        found[run_id] = Cleanable(run_id, RULE_REPLACED, reason, superseded_by=superseded_by,
                                  label=(working["runs"].get(run_id) or {}).get("label"))
    running = set() if jobs is None else {job.candidate_id for job in jobs.list()}
    for run_id in sorted(present):
        if run_id in found or run_id in working["runs"] or run_id in running:
            continue
        try:
            if binding.candidate_delta(run_id) is None:
                continue
            newest = binding.newest_runner_receipt(run_id)
        except _UNREADABLE:
            continue
        if newest is None:
            reason = "Its run never finished: no runner receipt was retained."
        elif newest[1].get("schema") == "RunnerRunReceipt@3" and newest[1].get("seat_execution_complete") is False:
            reason = "Its run never finished: the retained runner receipt reports incomplete seat execution."
        else:
            continue
        found[run_id] = Cleanable(run_id, RULE_FAILED, reason)
    return found


def plan_cleaning(binding: ProjectBinding, *, jobs: JobRegistry | None = None) -> CleaningPlan:
    """The runs that may go to the trash now, and why each other run the rules named stays.

    Reads only. A run the rules name stays unless every condition holds; one whose own reading fails
    stays with that failure as its reason.
    """

    def nothing(*warnings: str) -> CleaningPlan:
        return CleaningPlan((), {}, {}, tuple(warnings))

    working, _ = binding.repository.read_working_draft()
    graph = worktree_graph(binding, jobs=jobs)
    head = graph.head
    if head is None or head.origin != "working-position" or working["current"] != head.run_id:
        return nothing("The working position could not be read as the Working Head; nothing is cleaned.")
    store = admission_store(binding)
    if store.problems:
        return nothing("Admissions could not be read; nothing is cleaned.", *store.problems)
    kept_lines = set(head.lineage)
    # Every person's head and ancestry is live, even when the sweep was
    # triggered by someone else moving their own line. Fail closed on damage.
    try:
        for position in working.get("positions", {}).values():
            if position["current"] is not None:
                kept_lines.update(lineage_of(binding, position["current"]))
    except _UNREADABLE as exc:
        return nothing(f"A person's working line could not be read; nothing is cleaned: {_detail(exc)}")
    try:
        for branch_id in sorted(binding.repository.read_design_branches()):
            for _ref, stage in binding.design_history(branch_id):
                kept_lines.update(lineage_of(binding, stage.candidate_id))
    except _UNREADABLE as exc:
        return nothing(f"Design branches could not be read; nothing is cleaned: {_detail(exc)}")
    for run_id, claims in store.claims.items():
        if any(not claim.superseded and claim.outcome == ADMITTED for claim in claims):
            kept_lines.update(lineage_of(binding, run_id))
    considered = _considered(binding, graph, working, store, jobs)
    kept: dict[str, str] = {}
    runs: dict[str, Cleanable] = {}
    for run_id, cleanable in considered.items():
        try:
            why = _held(binding, run_id, working, store, kept_lines)
        except _UNREADABLE as exc:
            why = f"it could not be read: {_detail(exc)}"
        if why is None:
            runs[run_id] = cleanable
        else:
            kept[run_id] = why
    named_by: dict[str, set[str]] = {run_id: set() for run_id in runs}
    if runs:
        mentions = binding.repository.run_mentions(runs)
        admissions = f"run:{ADMISSIONS_RUN_ID}"
        for run_id, places in mentions.items():
            for place in sorted(places):
                if place == _WORKING_FILE:
                    continue
                if place == admissions:
                    try:
                        if _admission_mentions_explained(binding, run_id):
                            continue
                    except _UNREADABLE as exc:
                        kept.setdefault(run_id, f"its admissions could not be read: {_detail(exc)}")
                        continue
                if place.startswith("run:") and place[4:] in runs:
                    named_by[run_id].add(place[4:])
                    continue
                kept.setdefault(run_id, f"{place[4:] if place.startswith('run:') else place} names it")
        # A run some kept run names stays too, and so on until nothing more changes.
        changed = True
        while changed:
            changed = False
            for run_id in list(runs):
                why = kept.get(run_id) or next((f"{other} names it and stays" for other in sorted(named_by[run_id])
                                                if other not in runs), None)
                if why is not None:
                    kept[run_id] = why
                    del runs[run_id]
                    changed = True
    return CleaningPlan(tuple(runs[run_id] for run_id in sorted(runs)),
                        {run_id: frozenset(named_by[run_id] & set(runs)) for run_id in runs}, kept, tuple(graph.warnings))


# ---- moving, reading and restoring -------------------------------------------------------------------


def _state_digest(binding: ProjectBinding, run_id: str) -> str | None:
    try:
        newest = binding.newest_runner_receipt(run_id)
    except _UNREADABLE:
        return None
    digest = None if newest is None else newest[1].get("design_state_digest")
    return digest if isinstance(digest, str) and digest else None


def _retention_run(binding: ProjectBinding):
    if RETENTION_RUN_ID in binding.run_ids():
        return binding.load_run(RETENTION_RUN_ID)
    return binding.repository.create_run(RETENTION_RUN_ID)


def _retain_event(binding: ProjectBinding, *, action: str, occurred_at: str, actor_id: str, authenticated: bool,
                  origin: str, runs: list[dict[str, Any]], **extra: Any) -> None:
    """One ``AuditEvent@1`` in the retention run: who cleaned or restored which runs, and why."""

    payload = {
        "schema": _AUDIT_EVENT_SCHEMA, "eventId": f"aud-{uuid4().hex[:12]}", "occurredAt": occurred_at,
        "action": action, "status": "succeeded", "projectId": binding.project_id, "actorId": actor_id,
        "authenticatedActor": authenticated, "origin": origin, "runs": runs, **extra,
    }
    run = _retention_run(binding)
    binding.repository.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run.run_id),
                                record_kind=AUDIT_EVENT, payload=payload)


def clean_superseded(binding: ProjectBinding, *, now: datetime, jobs: JobRegistry | None = None,
                     trigger: str = "open", stopping: Callable[[], bool] = lambda: False) -> Sweep:
    """Move every run the plan allows into the trash: runs that name another go before it.

    A run the repository refuses, or cannot move whole, stays, and so does every run only it named.
    Once ``stopping`` says so, nothing more moves. One ``design.cleaned`` event names everything that moved.
    """

    moment = _iso(now)
    plan = plan_cleaning(binding, jobs=jobs)
    kept = dict(plan.kept)
    remaining = {cleanable.run_id: cleanable for cleanable in plan.runs}
    moved: list[TrashEntry] = []
    stayed: set[str] = set()
    while remaining:
        ready = sorted(run_id for run_id in remaining if not (plan.named_by.get(run_id, frozenset()) & set(remaining)))
        if not ready:
            for run_id in remaining:
                kept[run_id] = "it and another run name each other"
            break
        for run_id in ready:
            cleanable = remaining.pop(run_id)
            blocked = sorted(plan.named_by.get(run_id, frozenset()) & stayed)
            if blocked or stopping():
                kept[run_id] = f"{blocked[0]} names it and stayed" if blocked else "the Runtime is stopping"
                stayed.add(run_id)
                continue
            try:
                entry = binding.repository.trash_run(
                    run_id, now=moment, rule=cleanable.rule, reason=cleanable.reason,
                    state_digest=_state_digest(binding, run_id), superseded_by=cleanable.superseded_by,
                    base_run_id=cleanable.base_run_id or _base_of(binding, run_id), label=cleanable.label)
            except (RunNotTrashed, ProjectRepositoryError, OSError, ValueError) as exc:
                kept[run_id] = str(exc)
                stayed.add(run_id)
                continue
            moved.append(entry)
    warnings = list(plan.warnings)
    if moved:
        try:
            _retain_event(binding, action=DESIGN_CLEANED, occurred_at=moment, actor_id=RETENTION_ACTOR_ID,
                          authenticated=False, origin=ORIGIN_RUNTIME, trigger=trigger,
                          runs=[{"runId": entry.run_id, "rule": entry.rule, "reason": entry.reason,
                                 "supersededBy": entry.superseded_by, "baseRunId": entry.base_run_id,
                                 "stateDigest": entry.state_digest} for entry in moved])
        except _UNREADABLE as exc:
            warnings.append(f"The cleaning was not retained as an event: {_detail(exc)}")
            _LOG.warning("a design.cleaned event could not be retained: %s", exc)
    return Sweep(trigger, tuple(moved), (), kept, tuple(warnings))


def _base_of(binding: ProjectBinding, run_id: str) -> str | None:
    try:
        delta = binding.candidate_delta(run_id)
    except _UNREADABLE:
        return None
    source = None if delta is None else (delta.get("source_run_ref") or {}).get("run_id")
    return source if isinstance(source, str) else None


def purge_expired(binding: ProjectBinding, *, now: datetime) -> tuple[str, ...]:
    """Delete every trash entry older than the retention; the purged run ids."""

    return binding.repository.purge_trash(now=_iso(now))


def prune_recovery(binding: ProjectBinding, *, now: datetime) -> tuple[str, ...]:
    """Expire superseded local recovery snapshots; the removed snapshots' project-relative paths.

    The rule is the repository's (``prune_working_draft``), unchanged from when the
    Hub applied it: a snapshot goes only when it is not the current ``localDraftRef``
    and was not updated within 24 hours, one inconsistent snapshot refuses the
    whole expiry, and no run ever goes. A project without a working position has
    no recovery and is left as it was, without a lock file.
    """

    repository = binding.repository
    if repository.read_working_draft()[1] is None:
        return ()
    return repository.prune_working_draft(now=_iso(now))


def read_trash(binding: ProjectBinding) -> TrashView:
    """The project trash, oldest first; reading writes nothing."""

    return TrashView(binding.project_id, RETENTION_DAYS, binding.repository.trash_entries())


def restore_trashed(binding: ProjectBinding, run_id: str, *, now: datetime, actor_id: str, authenticated: bool,
                    origin: str) -> tuple[TrashEntry, ...]:
    """Restore one trashed run, and every trashed run it names, so it reads whole again; what came back.

    A draft built on another trashed draft names it in its change, so both come back. The restore is
    retained as a ``design.restored`` event, which names the run: it is never cleaned again.
    """

    require_identifier(run_id, "run_id")
    entries = {entry.run_id for entry in binding.repository.trash_entries()}
    if run_id not in entries:
        raise StudioError(404, "TRASH_ENTRY_NOT_FOUND", f"The project trash holds no run {run_id}.")
    restored: list[TrashEntry] = []
    pending, seen = [run_id], set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        try:
            restored.append(binding.repository.restore_trashed_run(current))
        except TrashEntryNotFound as exc:
            if current == run_id:
                raise StudioError(404, "TRASH_ENTRY_NOT_FOUND", str(exc)) from exc
            continue
        except RunNotRestored as exc:
            if current == run_id:
                raise StudioError(409, "TRASH_RESTORE_REFUSED", str(exc)) from exc
            _LOG.warning("a trashed run %s names stays in the trash: %s", current, exc)
            continue
        others = sorted(entries - seen)
        if others:
            named = binding.repository.run_mentions(others)
            pending.extend(other for other in others if f"run:{current}" in named[other])
    moment = _iso(now)
    try:
        _retain_event(binding, action=DESIGN_RESTORED, occurred_at=moment, actor_id=actor_id, authenticated=authenticated,
                      origin=origin, runs=[{"runId": entry.run_id, "trashedAt": entry.trashed_at} for entry in restored])
    except _UNREADABLE as exc:
        _LOG.warning("a design.restored event could not be retained: %s", exc)
    return tuple(restored)


# ---- when it runs ------------------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RetentionSweeps:
    """One Runtime process's automatic cleaning: at project open and after each Continue, one sweep at a time.

    ``at_open`` waits for the project index's first load, purges expired trash entries and cleans;
    ``after_continue`` cleans. Every sweep first expires superseded local recovery (``prune_recovery``),
    and from the open on a sweep that does only that comes every ``prune_interval_s`` (15 minutes) too.
    A request made while a sweep runs is folded into one more sweep after it. Every sweep runs on its
    own thread, so no request waits for it; ``wait`` is for tests.
    """

    def __init__(self, state: Any, *, clock: Callable[[], datetime] = _utc_now, index_wait_s: float = 60.0,
                 prune_interval_s: float = RECOVERY_PRUNE_INTERVAL_S) -> None:
        self._state = state
        self._clock = clock
        self._index_wait_s = index_wait_s
        self._prune_interval_s = prune_interval_s
        self._lock = threading.Lock()
        self._wanted: str | None = None
        self._thread: threading.Thread | None = None
        self._cadence: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._stopping = False
        self._stopped = threading.Event()
        # The last sweep, for diagnostics.
        self.last: Sweep | None = None

    def at_open(self) -> None:
        self._request("open")
        with self._lock:
            if self._stopping or self._cadence is not None:
                return
            self._cadence = threading.Thread(target=self._every_interval, name="studio-recovery-expiry", daemon=True)
            self._cadence.start()

    def after_continue(self) -> None:
        self._request("continue")

    def _every_interval(self) -> None:
        while not self._stopped.wait(self._prune_interval_s):
            self._request("prune")

    def _request(self, trigger: str) -> None:
        with self._lock:
            if self._stopping:
                return
            # The broader sweep stands for the narrower: an open also purges, and is never folded away.
            if self._wanted is None or _BREADTH[trigger] > _BREADTH[self._wanted]:
                self._wanted = trigger
            self._idle.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="studio-retention", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                trigger, self._wanted = self._wanted, None
                if trigger is None or self._stopping:
                    self._thread = None
                    self._idle.set()
                    return
            try:
                self.last = self.sweep(trigger)
            except Exception:  # noqa: BLE001 - a sweep that fails cleans nothing; the next one tries again
                _LOG.exception("the retention sweep (%s) failed", trigger)

    def sweep(self, trigger: str) -> Sweep:
        """One sweep on the calling thread: superseded recovery expired first, then the trash.

        After an open the index is waited for and the expired entries are purged
        before cleaning; a ``prune`` (the cadence) expires recovery and nothing else.
        """

        binding = bound_project(self._state)
        expired, warnings = self._expire_recovery(binding)
        if trigger == "prune":
            return Sweep(trigger, (), (), {}, warnings, expired)
        purged: tuple[str, ...] = ()
        if trigger == "open":
            binding.await_index(self._index_wait_s)
            purged = purge_expired(binding, now=self._clock())
        result = clean_superseded(binding, now=self._clock(), jobs=getattr(self._state, "jobs", None), trigger=trigger,
                                  stopping=lambda: self._stopping)
        if result.cleaned or purged:
            _LOG.info("project %s: %d runs moved to the trash, %d purged (%s)", binding.project_id,
                      len(result.cleaned), len(purged), trigger)
        return Sweep(result.trigger, result.cleaned, purged, result.kept, warnings + result.warnings, expired)

    def _expire_recovery(self, binding: ProjectBinding) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """``prune_recovery``, with a refusal kept as the sweep's warning: the project stays as it was."""

        try:
            expired = prune_recovery(binding, now=self._clock())
        except _UNREADABLE as exc:
            # One inconsistent snapshot refuses the whole expiry and removes nothing.
            _LOG.warning("superseded local recovery of %s was not expired: %s", binding.project_id, exc)
            return (), (f"Superseded local recovery was not expired: {_detail(exc)}",)
        if expired:
            _LOG.info("project %s: %d superseded local recovery snapshots expired", binding.project_id, len(expired))
        return expired, ()

    def wait(self, timeout: float | None = None) -> bool:
        """Whether every requested sweep has ended within ``timeout``."""

        return self._idle.wait(timeout)

    def stop(self, timeout: float = 10.0) -> None:
        """No sweep starts after this, and one running moves nothing more once its current run has moved."""

        with self._lock:
            self._stopping = True
            thread, cadence = self._thread, self._cadence
        self._stopped.set()
        for running in (cadence, thread):
            if running is not None and running is not threading.current_thread():
                running.join(timeout)
