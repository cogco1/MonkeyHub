"""The one project this process answers for, and the run that answers for it.

Binding is a kernel question: ``open_located_project`` finds the repository and
``read_head`` states the exact published version. Choosing the *reference run*
is the only judgement here, and it is made in the open: the request may name a
run, the operator may configure one, and otherwise the newest run that actually
finished design work wins. The choice and its source both travel on the wire so
no client has to guess which run a number belongs to.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import wraps
import logging
import os
from pathlib import Path, PurePosixPath
import threading
from typing import Any, Callable, Mapping, TypeVar
from uuid import uuid4
import weakref

from starlette.datastructures import State

from archflow.project.index import IndexKeeper, IndexState, ProjectIndex
from archflow.project.location import open_located_project
from archflow.project.memo import ContentMemo, PathStamps
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    DESIGN_STAGE,
    EQUIVALENCE_HARNESS_ENVELOPE,
    PROJECT_STAGE_WORKFLOW,
    RUNNER_RUN_RECEIPT,
    STAGE_RUN_ENVELOPE,
    STATE_RECORD,
    STUDIO_CANDIDATE_DELTA,
    STUDIO_CANDIDATE_ENVELOPE,
)
from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    record_ref_from_uri,
)
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectRepositoryError,
    write_serial,
)
from archflow.project.watch import LayoutLease, release_when_collected, watch_layout
from archflow.state.stage_workflow import HARNESS_WORKFLOW_IDS
from archflow.state.design_portfolio import DesignBranch, DesignStage
from archflow.state.state_record import Entity, StateRecord, StateRecordError

from ..settings import PROJECT_DIR_ENV, REFERENCE_RUN_ENV, StudioSettings
from ..transport.errors import StudioError, error_sentence

STAGE_WORKFLOW_SCHEMA = "ProjectStageWorkflow@1"
STAGE_ENVELOPE_SCHEMA = "StageRunEnvelope@1"
RUNNER_RECEIPT_V3 = "RunnerRunReceipt@3"

# The three record kinds a ``StageRunEnvelope@1`` is retained under: the
# project's own stage runs and the two harnesses of ADR-007 rule 4. A run
# states its stage - and so its phase - in exactly one of these.
STAGE_ENVELOPE_KINDS = (
    STAGE_RUN_ENVELOPE,
    STUDIO_CANDIDATE_ENVELOPE,
    EQUIVALENCE_HARNESS_ENVELOPE,
)

# ``HARNESS_WORKFLOW_IDS`` (imported above) names the runs whose workflow says
# they exist to compare or to answer the Studio, not to carry the design
# forward. They may be the newest complete runs in the project and must still
# never become its reference. The set lives in archflow.state.stage_workflow
# because the runner reads the same one to say, on the receipt of the closure
# it wrote, whether that closure belongs to a harness (ADR-007 rule 4).

# The run id a projection is bound to when the project holds no run that can
# answer for it. It names no run on disk, and the projection says so.
STUDIO_RUN_ID = "studio-projection"

# One identity per process. Nothing a process remembers survives its restart,
# so a restarted worker must not answer "not modified" to a tag it never gave.
READ_EPOCH = uuid4().hex
# Answers remembered per binding, least recently used first out.
MEMO_ENTRIES = 128
# How long a read waits for a loaded project index to apply this process's
# own writes before it reads the runs itself (ADR-008 phase 1b). Waiting is
# not work: the index's own thread projects, never the request's.
INDEX_CATCH_UP_S = 1.0

# Parsed State Records by content digest, shared by every binding in the
# process (ADR-008 phase 1a; ``ProjectBinding.state_record``). Sized by the
# records' JSON bytes: a parsed record holds about five times its file.
_STATE_RECORDS = ContentMemo("studio.state-records", max_entries=64, max_size=16 * 1024 * 1024)
# Committed Stage chains by project and the head and fork refs that fix them
# (``ProjectBinding.design_history``); every value is a tuple of frozen values.
_DESIGN_HISTORIES = ContentMemo("studio.design-histories", max_entries=256)
# Each run's part of the reference-run survey, under the stamps of what it
# read (``ProjectBinding._survey_run``).
_SURVEY_RUNS = ContentMemo("studio.survey-runs", max_entries=4096)
# What makes the survey skip a run rather than fail.
_SURVEY_UNREADABLE = (StudioError, ProjectRepositoryError, ValueError, OSError)

_T = TypeVar("_T")
_MISSING = object()
_LOG = logging.getLogger(__name__)
# ``bound_project``: one binding, and one index attached to it, per state.
_BINDING_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ReadToken:
    """What every answer derived from the project's files is derived under.

    Two answers computed under equal tokens read the same files. ``serial``
    moves with this process's own writes, ``fingerprint`` with anyone's, and
    ``stable`` says the fingerprint is old enough to be trusted to move and
    was taken after this process's last write.
    """

    epoch: str
    serial: int
    fingerprint: str
    stable: bool


@dataclass(frozen=True, slots=True)
class ReferenceRun:
    """The run a projection answers for, and how it came to be chosen."""

    run: RunRef
    source: str
    receipt: Mapping[str, Any] | None
    # Run directories the survey could not read. One corrupt run must not cost
    # the client every answer, but the skip is never silent.
    skipped_runs: tuple[str, ...] = ()
    # The chosen receipt names a workflow that would not load, so whether the
    # run was a harness is unknown rather than settled.
    workflow_unresolved: bool = False


def record_kind(ref: ProjectRecordRef) -> str | None:
    """The record kind in a P036 record name, or None if it is not one.

    The kind is compared by equality wherever this is used: a prefix test
    would let ``runner-run-receipt-summary`` answer as a run receipt.
    """

    try:
        return ref.record_kind
    except ValueError:
        return None


class ProjectBinding:
    """One opened P036 project. Read-only: it never writes and never issues.

    It remembers answers in memory, under a ``ReadToken`` that moves
    whenever what they were read from can have moved, and may read what the
    project index (``use_index``) keeps. The index is derived and outside the
    project, and its own thread writes it; this binding only reads it.
    """

    def __init__(
        self,
        repository: FilesystemProjectRepository,
        *,
        project_id: str,
        project_dir: Path,
        settings: StudioSettings,
    ) -> None:
        self.repository = repository
        self.project_id = project_id
        self.project_dir = project_dir
        # Kept so ``reference_run`` can honour the configured run without the
        # routes having to pass settings back in on every request.
        self.settings = settings
        # File identity, memoized per opened project. The key carries size and
        # modification time, so a file that changed is hashed again rather than
        # remembered wrongly.
        self.file_sha256_cache: dict[tuple[str, int, int], str] = {}
        # Read tokens and the answers kept under them (ADR-008 phase 0a).
        self._memo_lock = threading.Lock()
        self._memo: OrderedDict[tuple[ReadToken, tuple], Any] = OrderedDict()
        self._memo_token: ReadToken | None = None
        # This binding's share of the project's layout watch, taken on the
        # first read and given back by ``close`` (or once nobody holds the
        # binding any more).
        self._layout_lock = threading.Lock()
        self._layout_lease: LayoutLease | None = None
        self._layout_finalizer: weakref.finalize | None = None
        # The project index's keeper, when one is in use (ADR-008 phase 1b),
        # stopped by ``close`` or once nobody holds the binding any more.
        self._index_keeper: IndexKeeper | None = None
        self._index_finalizer: weakref.finalize | None = None

    def read_token(self, *, wait: bool = True) -> ReadToken | None:
        """The token an answer read from the project's files now is derived under.

        Read, never taken: the project's layout watch (``archflow.project.watch``)
        keeps the fingerprint current on a thread of its own, and this reads
        the latest one it published - no request ever walks the project. So
        this process's writes change the token at once through ``serial``, and
        a fingerprint published before the newest of them is not stable:
        nothing is kept or answered 304 under it until the watch has seen that
        write too. Anyone else's writes move ``fingerprint`` once the watch sees
        them, within about a second.

        Only the very first read waits, for the watch's first walk. With
        ``wait=False`` that read answers None instead, and so does any read
        before this binding has asked for the watch.
        """

        # Before the fingerprint: a write in between then leaves the serial
        # past the one that fingerprint was taken under.
        serial = write_serial(self.repository.layout.root)
        lease = self._layout_lease
        if lease is None:
            if not wait:
                return None
            lease = self.layout_watch()
        seen = lease.latest(wait=wait)
        if seen is None:
            return None
        return ReadToken(
            READ_EPOCH, serial, seen.fingerprint.digest, seen.fingerprint.stable and seen.serial == serial,
        )

    def layout_watch(self) -> LayoutLease:
        """This binding's lease on the project's layout watch, taken on first use.

        Every binding on one root shares that root's one watch. A test or a
        tool that changed the project behind the watch's back can ``sync`` it.
        """

        lease = self._layout_lease
        if lease is not None:
            return lease
        with self._layout_lock:
            if self._layout_lease is None:
                lease = watch_layout(self.repository.layout.root)
                self._layout_finalizer = release_when_collected(self, lease)
                self._layout_lease = lease
            return self._layout_lease

    def use_index(self, index: ProjectIndex) -> IndexKeeper:
        """Keep ``index`` current on a thread of its own and read from it once it is loaded.

        The keeper holds its own share of the layout watch and this process's
        write observer; it loads (reconciles or rebuilds) the file and applies
        every change in the background. Until it has loaded, and whenever it
        fails, every reader reads the project itself: opening the project
        never waits for the index.

        The keeper must not outlive the binding: ``close`` stops it, and so
        does collecting a binding nobody closed. Its projector therefore holds
        the binding weakly (``StudioProjector``).
        """

        with self._layout_lock:
            if self._index_keeper is not None:
                raise RuntimeError("this binding already has a project index")
            keeper = IndexKeeper(index, watch_layout(self.repository.layout.root), name=self.project_id)
            self._index_keeper = keeper
            self._index_finalizer = weakref.finalize(self, keeper.stop, wait=False)
        keeper.start()
        return keeper

    def await_index(self, timeout: float | None = None) -> IndexKeeper | None:
        """Wait for the index's first load to end; its keeper, or None when no index answers."""

        keeper = self._index_keeper
        if keeper is None or keeper.wait_loaded(timeout) is None:
            return None
        return keeper

    def index_state(self) -> IndexState | None:
        """The index's last commit, or None when no index answers: one attribute read."""

        keeper = self._index_keeper
        return None if keeper is None else keeper.state

    def index_status(self) -> str | None:
        """What the index is doing (``IndexKeeper.status``), or None when this binding keeps none."""

        keeper = self._index_keeper
        return None if keeper is None else keeper.status

    def index_reader(self, *, wait: float = INDEX_CATCH_UP_S) -> ProjectIndex | None:
        """The index when it holds every write this process made, else None: read the runs instead.

        A loaded index that is behind this process's newest write is waited
        for, up to ``wait`` seconds; one still loading is not.
        """

        keeper = self._index_keeper
        if keeper is None or keeper.wait_readable(wait) is None:
            return None
        return keeper.index

    def close(self) -> None:
        """Stop the index's keeper and give back this binding's share of the layout watch.

        The watch holds a handle on the project folder, so whoever is done
        with the project closes its binding. Reading again watches again; the
        index is not kept again until ``use_index`` is called again.
        """

        with self._layout_lock:
            keeper, self._index_keeper = self._index_keeper, None
            index_finalizer, self._index_finalizer = self._index_finalizer, None
        if index_finalizer is not None:
            index_finalizer.detach()
        if keeper is not None:
            keeper.stop()
        with self._layout_lock:
            lease, self._layout_lease = self._layout_lease, None
            finalizer, self._layout_finalizer = self._layout_finalizer, None
        if finalizer is not None:
            finalizer.detach()
        if lease is not None:
            lease.release()

    def memo(self, key: tuple, compute: Callable[[], _T]) -> _T:
        """``compute()``, remembered under the current token while it is stable.

        ``compute`` runs outside every lock, and must be a pure reading of the
        project's files: its answer is handed back again, the same object, to
        whoever asks under the same token.
        """

        token = self.read_token()
        if not token.stable:
            return compute()
        found = self.memo_get(token, key, _MISSING)
        if found is not _MISSING:
            return found
        value = compute()
        self.memo_put(token, key, value)
        return value

    def memo_get(self, token: ReadToken, key: tuple, default: Any = None) -> Any:
        """What ``memo_put`` kept under exactly this token and key, else ``default``."""

        entry = (token, key)
        with self._memo_lock:
            if entry not in self._memo:
                return default
            self._memo.move_to_end(entry)
            return self._memo[entry]

    def memo_put(self, token: ReadToken, key: tuple, value: Any) -> None:
        """Keep ``value`` under a stable token; an unstable one keeps nothing."""

        if not token.stable:
            return
        entry = (token, key)
        with self._memo_lock:
            if token != self._memo_token:
                # A token never comes back once the project moved on, so what
                # was kept under another one is dropped rather than left to age
                # out: a kept answer can be a whole design history.
                self._memo.clear()
                self._memo_token = token
            self._memo[entry] = value
            self._memo.move_to_end(entry)
            while len(self._memo) > MEMO_ENTRIES:
                self._memo.popitem(last=False)

    @classmethod
    def open(cls, settings: StudioSettings) -> ProjectBinding:
        """Open the configured project, or refuse and say what went wrong."""

        project_dir = Path(settings.project_dir)
        try:
            location, repository = open_located_project(
                project_dir.name,
                local_projects_root=project_dir.parent,
            )
        except Exception as exc:  # the reason belongs on the wire, not in a log
            raise StudioError(
                503,
                "PROJECT_NOT_BOUND",
                "the configured project could not be opened: "
                f"{error_sentence(exc)}. The Studio API binds the project "
                f"named by {PROJECT_DIR_ENV} or --project-dir; it never "
                "guesses one.",
            ) from exc
        return cls(
            repository,
            project_id=location.project_id,
            project_dir=location.root,
            settings=settings,
        )

    def head(self) -> ProjectVersionRef:
        """The version this project publishes right now: its current issue.

        Named after ``read_head`` and the repository file it reads, both of
        which keep their names because the format owns them (ADR-004,
        ADR-007). Everything a person reads calls this the published design.
        """

        return self.repository.read_head()

    def design_history(
        self, branch_id: str
    ) -> tuple[tuple[ProjectRecordRef, DesignStage], ...]:
        """The committed ancestors of one branch, oldest first.

        ``acceptance_attribution`` is an application-level audit extension of
        the retained ``DesignStage@1`` envelope. It is intentionally not part
        of the pure portfolio value: ``accepted_by`` remains the actor id and
        the acceptance application validates the extension when it needs it.
        """

        branches = self.repository.read_design_branches()
        if branch_id not in branches:
            raise StudioError(
                404,
                "DESIGN_BRANCH_NOT_FOUND",
                f"Design branch {branch_id!r} does not exist.",
            )
        branch = DesignBranch.from_dict(branches[branch_id])
        # A Stage names its parent by digest, so the head and fork refs fix
        # the whole chain: it is walked once per pair (``_DESIGN_HISTORIES``),
        # and each Stage file is still checked, head first, as the walk would.
        key = (self.project_id, branch.head_stage, branch.fork_stage)
        kept = _DESIGN_HISTORIES.get(key)
        if kept is not None:
            for retained, _ in reversed(kept):
                self.repository.require_json(retained)
            return kept
        ref: ProjectRecordRef | None = branch.head_stage
        history: list[tuple[ProjectRecordRef, DesignStage]] = []
        seen: set[ProjectRecordRef] = set()
        while ref is not None:
            if ref in seen:
                raise StudioError(
                    409,
                    "DESIGN_HISTORY_INVALID",
                    "The committed design history contains a cycle.",
                )
            seen.add(ref)
            payload = self.repository.load_json(ref)
            if (
                record_kind(ref) != DESIGN_STAGE
                or payload.get("schema") != "DesignStage@1"
            ):
                raise StudioError(
                    409,
                    "DESIGN_HISTORY_INVALID",
                    "The design history names an invalid stage record.",
                )
            stage = DesignStage.from_dict(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"schema", "acceptance_attribution"}
                }
            )
            if stage.project_id != self.project_id:
                raise StudioError(
                    409,
                    "DESIGN_HISTORY_INVALID",
                    "The stage belongs to another project.",
                )
            history.append((ref, stage))
            ref = stage.parent_stage
        if branch.fork_stage not in seen:
            raise StudioError(
                409,
                "DESIGN_HISTORY_INVALID",
                "The branch history does not reach its fork stage.",
            )
        walked = tuple(reversed(history))
        _DESIGN_HISTORIES.put(key, walked)
        return walked

    def design_stage(self, ref: ProjectRecordRef) -> DesignStage:
        """Resolve a committed node; a prepared but unreferenced record is not one."""
        if ref.project_id != self.project_id:
            raise StudioError(
                409,
                "DESIGN_STAGE_MISMATCH",
                "The stage belongs to another project.",
            )
        for branch_id in self.repository.read_design_branches():
            for retained_ref, stage in self.design_history(branch_id):
                if retained_ref == ref:
                    return stage
        raise StudioError(
            404,
            "DESIGN_STAGE_NOT_FOUND",
            "This stage is not part of committed design history.",
        )

    def candidate_delta(self, run_id: str) -> dict[str, Any] | None:
        """One actual run's retained change, without inventing legacy deltas."""
        refs = [
            ref
            for ref in self.record_refs(run_id)
            if record_kind(ref) == STUDIO_CANDIDATE_DELTA
        ]
        if not refs:
            return None
        if len(refs) != 1:
            raise StudioError(
                409,
                "CANDIDATE_DELTA_INVALID",
                "This candidate has competing retained changes.",
            )
        payload = self.repository.load_json(refs[0])
        if (
            payload.get("schema") != "StudioCandidateDelta@1"
            or payload.get("project_id") != self.project_id
            or payload.get("run_id") != run_id
        ):
            raise StudioError(
                409,
                "CANDIDATE_DELTA_INVALID",
                "The retained change has a different project or run binding.",
            )
        return payload

    def run_ids(self) -> tuple[str, ...]:
        """Every run directory in the project, in name order."""

        runs = self.repository.layout.runs
        if not runs.is_dir():
            return ()
        return tuple(sorted(item.name for item in runs.iterdir() if item.is_dir()))

    def load_run(self, run_id: str) -> RunRef:
        """The named run, or a 404 that repeats the name it was given.

        The detail names the project and the run and stops there. Where this
        service keeps the project on disk is an operator's question, answered
        by ``projectDir`` on ``GET /api/project``; it is no part of an answer
        about a run, and a refusal is read by whoever ran into it.
        """

        try:
            return self.repository.load_run(run_id)
        except Exception as exc:
            raise StudioError(
                404,
                "RUN_NOT_FOUND",
                f"{self.project_id}: run {run_id!r} does not exist in the "
                f"bound project: {error_sentence(exc)}",
            ) from exc

    def record_refs(self, run_id: str, *, kind: str | None = None) -> tuple[ProjectRecordRef, ...]:
        """Retained JSON records of one run, optionally of one exact kind."""

        return self.repository.list_json(
            run=self.load_run(run_id),
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run_id,
            ),
            record_kind=kind,
        )

    def newest_runner_receipt(
        self, run_id: str
    ) -> tuple[ProjectRecordRef, Mapping[str, Any]] | None:
        """One run's newest ``runner-run-receipt``, with the record it is in.

        The ref travels beside the payload because the receipt does not name
        itself on disk: the runner writes its own ``receipt_ref`` into the
        mapping it returns and not into the record it retains, so this ref is
        the only thing a caller can name the evidence with.

        This is the one place a run's receipt is chosen. Everything that asks
        "which receipt does this run answer with" asks here, so a second reader
        cannot start preferring a different one.
        """

        newest: tuple[float, ProjectRecordRef, Mapping[str, Any]] | None = None
        for ref, payload in self._receipts_of(run_id):
            try:
                mtime = self._mtime(ref)
            except OSError:
                # Listed and then gone. A record nobody can stat cannot be
                # this run's newest receipt, and a file disappearing between
                # two reads is not a bug in the reader.
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, ref, payload)
        return None if newest is None else (newest[1], newest[2])

    def exact_state_record(
        self, reference: ReferenceRun
    ) -> tuple[ProjectRecordRef, StateRecord]:
        """Load the exact retained State Record named by a run receipt.

        A run id and a content digest are not enough to establish lineage:
        ``StateRecord.digest`` deliberately excludes the run and base.  This
        reader therefore checks the receipt, P036 reference, retained payload,
        run identity and exact canonical base together before returning it.
        """

        receipt = reference.receipt
        if receipt is None:
            raise _reference_state_not_exact(
                reference,
                "the reference run has no runner receipt",
            )
        if (
            receipt.get("project_id") != self.project_id
            or receipt.get("run_id") != reference.run.run_id
        ):
            raise _reference_state_not_exact(
                reference,
                "the runner receipt names a different project or run",
            )
        uri = receipt.get("state_record_ref")
        try:
            ref = record_ref_from_uri(uri, self.project_id)
        except (TypeError, ValueError) as exc:
            raise _reference_state_not_exact(
                reference,
                f"state_record_ref is not a record in this project: {error_sentence(exc)}",
            ) from exc
        expected_parent = PurePosixPath(
            "runs",
            reference.run.run_id,
            "records",
        )
        path = PurePosixPath(ref.relative_path)
        if path.parent != expected_parent or record_kind(ref) != STATE_RECORD:
            raise _reference_state_not_exact(
                reference,
                "state_record_ref does not name this run's retained state-record",
            )
        try:
            record = self.state_record(ref)
        except Exception as exc:
            raise _reference_state_not_exact(
                reference,
                f"the retained state record could not be verified: {error_sentence(exc)}",
            ) from exc
        try:
            exact_run = record.run_ref
        except (StateRecordError, ValueError) as exc:
            raise _reference_state_not_exact(
                reference,
                f"the retained state record carries no exact run identity: {error_sentence(exc)}",
            ) from exc
        if exact_run != reference.run:
            raise _reference_state_not_exact(
                reference,
                "the retained state record's run or canonical base differs from the run manifest",
            )
        claimed_digest = receipt.get("state_record_digest")
        if (
            not isinstance(claimed_digest, str)
            or claimed_digest != record.digest
        ):
            raise _reference_state_not_exact(
                reference,
                "state_record_digest does not match the retained state record",
            )
        return ref, record

    def state_record(self, ref: ProjectRecordRef) -> StateRecord:
        """The State Record retained at ``ref``, parsed once per content digest.

        A record's digest names its bytes, and ``StateRecord.from_dict`` is a
        pure function of them, so one parsed record answers for every ref with
        that digest, in every binding of the process (``_STATE_RECORDS``).
        The instance is shared: nobody may change it, and what it keeps
        (``digest``, ``dependency_edges``) is taken once. The ref itself is
        still checked on every call, exactly as ``load_json`` checks it, and
        refused the same way.
        """

        key = (ref.sha256,) if isinstance(ref, ProjectRecordRef) else None
        found = None if key is None else _STATE_RECORDS.get(key)
        if found is not None:
            self.repository.require_json(ref)
            return found
        record = StateRecord.from_dict(self.repository.load_json(ref))
        _STATE_RECORDS.put(key, record, size=self.repository.require_json(ref))
        return record

    def _mtime(self, ref: ProjectRecordRef) -> float:
        """When the record was last written; the only ordering P036 offers.

        The receipt carries no authored timestamp, so "newest" can only mean
        newest on disk — a kernel card, not a choice made here.
        """

        return self.repository.record_stat(ref).st_mtime

    def _survey(
        self,
    ) -> tuple[
        tuple[str, ProjectRecordRef, Mapping[str, Any]] | None,
        tuple[str, ...],
    ]:
        """Survey the runs for a reference, reporting what could not be read.

        Choosing a reference run is a survey, not a transaction: one run
        directory without a manifest, or with a record the repository refuses,
        must not cost the client every other answer in the project. Such a run
        is skipped and named, and the projection says how many were skipped.

        Each run's part of the survey is kept per run (``_survey_run``); the
        chosen receipt is loaded for the caller.
        """

        newest: tuple[float, str, ProjectRecordRef] | None = None
        skipped: list[str] = []
        for run_id in self.run_ids():
            offered = self._survey_run(run_id)
            if offered is None:
                skipped.append(run_id)
                continue
            for mtime, ref in offered:
                if newest is None or mtime > newest[0]:
                    newest = (mtime, run_id, ref)
        if newest is None:
            return None, tuple(skipped)
        try:
            payload = self.repository.load_json(newest[2])
        except (ProjectRepositoryError, ValueError, OSError):
            # Read in the survey, gone now: survey again, reading every run.
            return self._survey_runs_uncached()
        return (newest[1], newest[2], payload), tuple(skipped)

    def _survey_runs_uncached(
        self,
    ) -> tuple[tuple[str, ProjectRecordRef, Mapping[str, Any]] | None, tuple[str, ...]]:
        newest: tuple[float, str, ProjectRecordRef, Mapping[str, Any]] | None = None
        skipped: list[str] = []
        for run_id in self.run_ids():
            try:
                offered = self._read_survey_run(run_id)[0]
            except _SURVEY_UNREADABLE:
                skipped.append(run_id)
                continue
            for mtime, ref, payload in offered:
                if newest is None or mtime > newest[0]:
                    newest = (mtime, run_id, ref, payload)
        return (None if newest is None else (newest[1], newest[2], newest[3])), tuple(skipped)

    def _survey_run(self, run_id: str) -> tuple[tuple[float, ProjectRecordRef], ...] | None:
        """One run's part of the survey: its complete design receipts and their times, in listing order.

        None when the run could not be read: the whole of one run's reading
        is inside the tolerance, not just its listing. A record that vanishes
        between being listed and being stat'ed is the same kind of accident as
        a run with no manifest, and both are skipped and named rather than
        fatal; whatever such a run offered came from a reading that did not
        finish, so none of it is offered. A run's part is kept
        (``_SURVEY_RUNS``) under the stamps of its manifest, its records
        directory and every receipt and workflow file it opened - a receipt's
        time is what decides which one is newest.
        """

        runs = self.repository.layout.runs
        key = (os.path.normcase(os.fspath(runs)), run_id)
        stamps = PathStamps()
        stamps.file(runs / run_id / "run.json")
        stamps.directory(runs / run_id / "records")
        stamp = stamps.value()
        kept = None if stamp is None else _SURVEY_RUNS.get(key)
        if kept is not None and kept[0] == stamp:
            check = PathStamps()
            check.files(kept[1])
            if check.value() == kept[2]:
                return kept[3]
        try:
            offered, opened = self._read_survey_run(run_id)
        except _SURVEY_UNREADABLE:
            return None
        part = tuple((mtime, ref) for mtime, ref, _payload in offered)
        if stamp is not None:
            check = PathStamps()
            check.files(opened)
            opened_stamp = check.value()
            if opened_stamp is not None:
                _SURVEY_RUNS.put(key, (stamp, opened, opened_stamp, part))
        return part

    def _read_survey_run(
        self, run_id: str,
    ) -> tuple[tuple[tuple[float, ProjectRecordRef, Mapping[str, Any]], ...], tuple[Path, ...]]:
        root = self.repository.layout.root
        offered: list[tuple[float, ProjectRecordRef, Mapping[str, Any]]] = []
        opened: list[Path] = []
        for ref, payload in self._receipts_of(run_id):
            opened.append(root / ref.relative_path)
            workflow = _record_uri_path(root, payload.get("workflow_ref"), self.project_id)
            if workflow is not None:
                opened.append(workflow)
            if not _is_complete(payload):
                continue
            if self._is_harness(payload):
                continue
            offered.append((self._mtime(ref), ref, payload))
        return tuple(offered), tuple(opened)

    def reference_run(self, run_id: str | None = None) -> ReferenceRun:
        """Resolve which run answers: the request, the operator, then the rule."""

        if run_id is not None:
            # An explicitly named run must exist; the survey's tolerance is for
            # runs nobody asked about.
            return self._chosen(self.load_run(run_id), "query", run_id)
        branches = self.repository.read_design_branches()
        if "main" in branches:
            head_ref = ProjectRecordRef.from_dict(
                branches["main"]["head_stage"]
            )
            stage = self.design_stage(head_ref)
            return ReferenceRun(
                self.load_run(stage.candidate_id),
                "rule",
                self.repository.load_json(stage.runner_ref),
            )
        configured = self.settings.reference_run
        if configured is not None:
            try:
                run = self.load_run(configured)
            except StudioError as exc:
                raise StudioError(
                    404,
                    "RUN_NOT_FOUND",
                    f"{REFERENCE_RUN_ENV} names run {configured!r}, which does "
                    f"not exist in the bound project: {exc.detail}",
                ) from exc
            return self._chosen(run, "config", configured)
        chosen, skipped = self._survey()
        if chosen is None:
            # No run has finished design work here. Rather than refuse to
            # describe the project, bind to a run id that claims nothing.
            return ReferenceRun(
                RunRef(self.project_id, STUDIO_RUN_ID, self.head()),
                "none",
                None,
                skipped_runs=skipped,
            )
        return ReferenceRun(
            self.load_run(chosen[0]),
            "rule",
            chosen[2],
            skipped_runs=skipped,
            workflow_unresolved=self._workflow_unresolved(chosen[2]),
        )

    def _chosen(
        self,
        run: RunRef,
        source: str,
        run_id: str,
    ) -> ReferenceRun:
        """A run the caller named, with whatever receipt it happens to hold.

        A run that loads and whose records the repository then refuses is a
        fact about that run, not a bug: it arrives as the 404 that names it
        rather than as an uncaught repository error. A run that simply holds
        no receipt yet is not refused — it exists, which is all a named run
        was ever asked to be — and answers with no receipt.

        The survey still runs, for its confession alone: a projection that
        skipped a run directory says so whether the run it answers for was
        named in the request or chosen by the rule.
        """

        try:
            receipt = self._newest_receipt_of(run_id)
        except (ProjectRepositoryError, ValueError, OSError) as exc:
            raise StudioError(
                404,
                "RUN_NOT_FOUND",
                f"{self.project_id}: run {run_id!r} exists in the bound "
                f"project and its records could not be read: "
                f"{error_sentence(exc)}",
            ) from exc
        return ReferenceRun(
            run,
            source,
            receipt,
            skipped_runs=self._survey()[1],
            workflow_unresolved=(
                False
                if receipt is None
                else self._workflow_unresolved(receipt)
            ),
        )

    # ---- the rule's own reading of the run records

    def _receipts_of(
        self, run_id: str
    ) -> tuple[tuple[ProjectRecordRef, Mapping[str, Any]], ...]:
        return tuple(
            (ref, self.repository.load_json(ref))
            for ref in self.record_refs(run_id, kind=RUNNER_RUN_RECEIPT)
            if record_kind(ref) == RUNNER_RUN_RECEIPT
        )

    def _newest_receipt_of(self, run_id: str) -> Mapping[str, Any] | None:
        newest = self.newest_runner_receipt(run_id)
        return None if newest is None else newest[1]

    def _load_workflow(
        self, receipt: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """The stage workflow the receipt names, or None if it does not resolve."""

        workflow_ref = receipt.get("workflow_ref")
        if workflow_ref is None:
            return None
        workflow = self._load_uri(workflow_ref)
        if workflow is None or workflow.get("schema") != STAGE_WORKFLOW_SCHEMA:
            return None
        return workflow

    def _is_harness(self, receipt: Mapping[str, Any]) -> bool:
        """Whether the receipt's workflow says, positively, that it is a harness.

        Harness runs are defined by what their workflow *is*, never by what it
        might be: a receipt naming no workflow (``RunnerRunReceipt@1``), or one
        whose workflow will not load, stays eligible. Excluding the unknown
        would let one unreadable record silently disqualify a real design run.
        """

        workflow = self._load_workflow(receipt)
        return (
            workflow is not None
            and workflow.get("workflow_id") in HARNESS_WORKFLOW_IDS
        )

    def _workflow_unresolved(self, receipt: Mapping[str, Any]) -> bool:
        """The receipt names a workflow the project cannot produce."""

        return (
            receipt.get("workflow_ref") is not None
            and self._load_workflow(receipt) is None
        )

    def retained_stage_phase(self, run_id: str) -> str | None:
        """The phase the stage envelope retained in one run states, verbatim.

        ADR-007 rule 1: a stage is a property of the run, stated by the
        envelope retained in it. A run receipt's ``stage.phase`` is a copy of
        this value; this is the record that made it. ``None`` when the run
        retains no envelope, retains more than one (two stages on disk is not
        a question this reader answers), or when the envelope states no phase.
        """

        found: list[str] = []
        try:
            refs = self.record_refs(run_id)
        except (StudioError, ProjectRepositoryError, ValueError, OSError):
            return None
        for ref in refs:
            if record_kind(ref) not in STAGE_ENVELOPE_KINDS:
                continue
            try:
                payload = self.repository.load_json(ref)
            except Exception:
                continue
            if payload.get("schema") != STAGE_ENVELOPE_SCHEMA:
                continue
            stage = payload.get("stage")
            phase = (
                stage.get("phase")
                if isinstance(stage, Mapping)
                else None
            )
            if isinstance(phase, str):
                found.append(phase)
        return found[0] if len(found) == 1 else None

    def frozen_workflow_first_phase(self) -> str | None:
        """The phase of stage zero of the project's own frozen workflow, verbatim.

        A ``project-stage-workflow`` retained anywhere in the project is the
        ladder the project froze (ADR-007 rule 2); the harness workflows are
        not one and are skipped. ``None`` when no such workflow is retained,
        or when the project froze more than one distinct ladder - which is a
        question for whoever froze them, not something to pick between here.
        """

        phases: set[str] = set()
        for run_id in self.run_ids():
            try:
                refs = self.record_refs(run_id, kind=PROJECT_STAGE_WORKFLOW)
            except (StudioError, ProjectRepositoryError, ValueError, OSError):
                continue
            for ref in refs:
                if record_kind(ref) != PROJECT_STAGE_WORKFLOW:
                    continue
                try:
                    payload = self.repository.load_json(ref)
                except Exception:
                    continue
                if payload.get("schema") != STAGE_WORKFLOW_SCHEMA:
                    continue
                if payload.get("workflow_id") in HARNESS_WORKFLOW_IDS:
                    continue
                stages = payload.get("stages")
                first = (
                    stages[0]
                    if isinstance(stages, list) and stages
                    else None
                )
                phase = (
                    first.get("phase")
                    if isinstance(first, Mapping)
                    else None
                )
                if isinstance(phase, str):
                    phases.add(phase)
        return next(iter(phases)) if len(phases) == 1 else None

    def _load_uri(self, uri: object) -> Mapping[str, Any] | None:
        """Load a ``project://`` record reference, or None if it does not resolve."""

        try:
            ref = record_ref_from_uri(uri, self.project_id)
        except (TypeError, ValueError):
            return None
        try:
            return self.repository.load_json(ref)
        except Exception:
            return None


def bound_project(state: State) -> ProjectBinding:
    """The process's one binding, opened on first use and kept.

    A failed open is not remembered: a project that appears after the service
    started binds on the next request instead of needing a restart.
    """

    binding = getattr(state, "binding", None)
    if binding is not None:
        return binding
    return _open_bound_project(state, read_runs=False)


def prepare_bound_project(state: State) -> None:
    """Open the process's binding ahead of its first request, reading every run once (#449).

    A cold process's first reads - the working source, worktrees, artifacts
    and design history a workspace asks for together - each list the same
    runs' records and survey the same receipts while the in-memory memos are
    still empty, and on one interpreter they queue behind each other doing
    it. Here that is done once, while ``_BINDING_LOCK`` is held, so a request
    arriving meanwhile waits for it instead of walking the runs beside it.
    With a project index attached the index answers instead. The memos
    stay keyed by the files' stamps, so nothing read here answers after the
    project moved. A project that does not open is not remembered, as in
    ``bound_project``, and neither is a run that does not read: each request
    still reads and refuses for itself.
    """

    if getattr(state, "binding", None) is None:
        _open_bound_project(state, read_runs=True)


def _open_bound_project(state: State, *, read_runs: bool) -> ProjectBinding:
    # Two first requests must not open two bindings, each with an index of its
    # own: the one that lost ``index.lock`` could be the one kept.
    with _BINDING_LOCK:
        binding = getattr(state, "binding", None)
        if binding is None:
            binding = ProjectBinding.open(state.settings)
            index_dir = getattr(state.settings, "project_index_dir", None)
            if index_dir is not None:
                # Imported here: the projector reads through the artifacts
                # module, which itself reads through this one.
                from .index import attach_project_index

                try:
                    attach_project_index(binding, index_dir)
                except Exception:  # noqa: BLE001 - the index is derived; P036 still answers
                    _LOG.exception("project index of %s could not be attached", binding.project_id)
            elif read_runs:
                # The layout watch's first walk, which the first read token
                # waits for, the runs' records, then their receipts' survey.
                binding.read_token()
                for run_id in binding.run_ids():
                    try:
                        binding.record_refs(run_id)
                    except _SURVEY_UNREADABLE:
                        continue
                try:
                    binding._survey()
                except _SURVEY_UNREADABLE:
                    pass
            state.binding = binding
    return binding


def release_bound_project(state: State) -> None:
    """Close the process's binding, if any, so that the next request opens the project again.

    For whoever changed the project under it wholesale (a shared-project
    pull). Closing stops the index's keeper and gives up ``index.lock`` before
    the next binding can be opened, so that one keeps the index again.
    """

    with _BINDING_LOCK:
        binding = getattr(state, "binding", None)
        state.binding = None
        if binding is not None:
            binding.close()


def initialize_modeling(binding: ProjectBinding) -> bool:
    """Prepare a genuinely empty project's first candidate, including old Board projects.

    This is an explicit write action, never part of a projection or binding read.
    Existing authored design and retained model records are left alone. The seed
    declares a modeling root and zero datum, not geometry or a design decision.
    """

    from archflow.project.inputs import (
        AuthoredRecordInvalid,
        AuthoredRecordMissing,
        load_authored_record,
    )
    from ..adapters.harness import HARNESS_PHASE

    repository = binding.repository
    head = repository.read_head()
    # Connecting an existing design must never reset it, even if its authored
    # file is missing or stale relative to a retained candidate or Stage.
    if (
        head.version != 0
        or repository.read_design_branches()
        or any(
            record_kind(ref)
            in {
                STATE_RECORD,
                RUNNER_RUN_RECEIPT,
                STUDIO_CANDIDATE_DELTA,
            }
            for run_id in binding.run_ids()
            for ref in binding.record_refs(run_id)
        )
    ):
        return False
    try:
        current = load_authored_record(repository).record
    except AuthoredRecordMissing:
        current = None
    except AuthoredRecordInvalid as exc:
        raise StudioError(422, "STATE_RECORD_INVALID", str(exc)) from exc
    if current is not None:
        empty = StateRecord(
            project_id=binding.project_id,
            run_id=current.run_id,
            entities=(),
        )
        if current.to_dict() != empty.to_dict():
            return False
    evidence = "input:monkeyarch-modeling-setup"
    record = StateRecord(
        project_id=binding.project_id,
        run_id="authored",
        entities=(
            Entity(
                "model",
                "Component@1",
                # Unclassified: the only component a fresh project shows is
                # not a template for classifying new parts (#408).
                fields={
                    "intent": "Root for candidate modeling",
                    "source_refs": [evidence],
                },
            ),
            Entity(
                "ground",
                "Level@1",
                fields={
                    "role": "ground",
                    "elevation": 0.0,
                },
                basis_refs=(evidence,),
            ),
        ),
        evidence_refs=(evidence,),
        option={"option_id": "modeling"},
    )
    seats = {
        "schema": "RunnerSeats@1",
        "commitment_ref": "commitment:monkeyarch-candidate-modeling",
        "branch_id": "runner-v1",
        "seats": [
            {
                "seat_id": "modeler",
                "disciplines": ["structure_support"],
                "phases": [HARNESS_PHASE.value],
                "owned_component_ids": ["model"],
                "consumes": [],
                "reviewer": False,
            }
        ],
    }
    try:
        return repository.initialize_authored_inputs(
            expected_head=head,
            expected_record=(
                None if current is None else current.to_dict()
            ),
            authored_record=record.to_dict(),
            seat_pack=seats,
        )
    except ProjectRepositoryError as exc:
        raise StudioError(
            409,
            "MODELING_INITIALIZATION_CONFLICT",
            str(exc),
        ) from exc


def _reference_state_not_exact(
    reference: ReferenceRun,
    detail: str,
) -> StudioError:
    return StudioError(
        409,
        "REFERENCE_STATE_NOT_EXACT",
        f"reference run {reference.run.run_id!r} is inspectable but not "
        f"actionable: {detail}",
    )


def resolve_project(state: State, project_id: str) -> ProjectBinding:
    """The binding a project-scoped path names, or a 404 that repeats the name.

    ``/api/projects/{project_id}/…`` is the general form of every resource in
    the protocol, and this is where that path segment turns into a project.
    One process binds one project today, so exactly one id resolves and every
    other is ``PROJECT_NOT_FOUND``. A server that binds several implements this
    function differently and changes no route and no client.
    """

    binding = bound_project(state)
    if project_id == binding.project_id:
        return binding
    raise StudioError(
        404,
        "PROJECT_NOT_FOUND",
        f"this server binds no project {project_id!r}. It binds "
        f"{binding.project_id}, which is also the project the unscoped paths "
        "answer for; GET /api/projects lists what there is.",
    )


def _record_uri_path(root: Path, uri: object, project_id: str) -> Path | None:
    """The file a ``project://`` record URI of this project names, or None if it names none."""

    try:
        return root / record_ref_from_uri(uri, project_id).relative_path
    except (TypeError, ValueError):
        return None


def _is_complete(receipt: Mapping[str, Any]) -> bool:
    """Whether the run this receipt describes actually finished its seats."""

    if receipt.get("schema") == RUNNER_RECEIPT_V3:
        return bool(receipt.get("seat_execution_complete"))
    return bool(receipt.get("accepted"))


def retained_sources(operation):
    """Keep exact-source validation and its retained write atomic with cleanup."""
    @wraps(operation)
    def guarded(binding: ProjectBinding, *args, **kwargs):
        with binding.repository.working_draft_guard():
            return operation(binding, *args, **kwargs)
    return guarded
