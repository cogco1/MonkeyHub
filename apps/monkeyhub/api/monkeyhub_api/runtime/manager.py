"""Live project attachments and observed operations over the existing Studio/P036.

No operation is replayed by a watcher or by recovery. A lost HTTP response is
reconciled against retained results; absence of proof remains visible.
"""

from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4, uuid5, NAMESPACE_URL

from archflow.project.record_kinds import STUDIO_DOCUMENT_MODEL_SOURCE, STUDIO_SOURCE_DOCUMENT
from archflow.project.repository import ProjectRepositoryError
from project_runtime.application.artifacts import (
    WORK_COPY_WORKSPACE,
    DocumentWorkCopy,
    document_bytes,
    list_document_work_copies,
)
from project_runtime.binding import ProjectBinding, ReadToken
from project_runtime.events import StudioEvents
from project_runtime.errors import StudioError

from ..projects import _project
from ..models import HubError, HubFailure
from .models import HubRuntimeDto, OperationRecord, ProjectRuntimeDto, WorkerStatus
from .operations import _ACTIVE, _CANDIDATE_REQUEST, _PROPOSAL_CANDIDATE, OperationManager
from .worker_http import HttpResult, _WorkerEvents, request_http
from .workers import project_key


_IDLE_RETAINED_REFRESH_S = 30
# How long the project observer waits between passes (#435). A pass while
# anything is in motion - an operation or job, a worker between states, a
# closing runtime, an observed work copy - follows it every second. Otherwise
# nothing it compares changes without a wake: the supervisor, the chat store
# and an attaching client end the wait at once, and a Hub mutation asks for a
# full read. The idle wait only bounds what a missed wake could delay.
_ACTIVE_HEARTBEAT_S = 1
_IDLE_HEARTBEAT_S = 5
# Worker states that stay put until the supervisor says otherwise.
_SETTLED_WORKER_STATES = {"ready", "stopped", "crashed", "unavailable"}
_WORKING_CLEANUP_INTERVAL_S = 15 * 60
# A sample must remain unchanged for this interval before bytes are read.
# File timestamps alone cannot measure that wait: producers can preserve them.
_WORK_COPY_SETTLED_NS = 2_000_000_000
# Unchanged stat metadata is only a fast path, not proof of unchanged content.
# Recheck occasionally for in-place saves that preserve both size and mtime.
_WORK_COPY_CONTENT_REFRESH_NS = _IDLE_RETAINED_REFRESH_S * 1_000_000_000
# The record kinds that decide which documents exist (project_runtime.documents
# list_documents; a drawing's receipt only adds who made it and why). If
# another kind ever decides that, the work-copy key below has to name it too.
_DOCUMENT_RECORD_KINDS = (STUDIO_SOURCE_DOCUMENT, STUDIO_DOCUMENT_MODEL_SOURCE)
# How often an unwoken watcher asks the project's read token whether that key
# can have moved. The key is read again only when the token moved, or had not
# settled when it was last read (#599). A check asks at once; a Hub mutation
# reads the key at once.
_WORK_COPY_CHECK_S = _IDLE_RETAINED_REFRESH_S
# How many of each project's Studio events the Hub keeps to open a new page's panel with.
_STUDIO_REPLAY = 200


def binding_signature(project_dir: str) -> tuple[int, int, int, bool] | None:
    """Two stats that move whenever the folder could stop being the project it was.

    The identity, size and time of ``project.json``, and whether ``HEAD``
    exists. ``None`` when the manifest cannot be stat'ed at all.
    """

    root = Path(project_dir)
    try:
        manifest = os.stat(root / "project.json")
    except OSError:
        return None
    return manifest.st_size, manifest.st_mtime_ns, manifest.st_ino, (root / "HEAD").exists()


def worker_dto(row) -> WorkerStatus:
    return WorkerStatus(
        workerId=row.worker_id, serviceId=row.service_id, projectId=row.project_id,
        projectDir=row.project_dir, instanceId=row.instance_id, processId=row.process_id,
        desiredState=row.desired_state, state=row.state, healthy=row.healthy,
        url=row.url, error=row.error,
    )


@dataclass
class _WorkCopyObservation:
    """What this process has seen of one explicitly opened work copy.

    ``observed_sha256`` is the only thing that decides whether the file moved:
    a page replaced through some other client does not make an untouched copy
    an edit, and an edit back to bytes the project already registered is still
    an edit. ``failure`` keeps the last refusal for that copy visible until it
    registers something or stops being bound; it is per copy, so one unreadable
    file never speaks for the project.
    """

    copy: DocumentWorkCopy
    sample: tuple[int, int, int, int] | None = None
    sample_since_ns: int | None = None
    # Both observation times use the monotonic clock, independent of file dates.
    hashed_at_ns: int | None = None
    observed_sha256: str | None = None
    failure: HubError | None = None


class _Wake(threading.Event):
    """The project observer's one wait.

    ``set`` asks the next pass for a full retained read, as a Hub mutation or
    a first open does. ``check`` asks it to read again now only if the project
    moved since its last read, as opening an observed project again does.
    ``nudge`` only ends the wait, so the next pass compares its small status
    values now without reading the project again.
    """

    def __init__(self) -> None:
        super().__init__()
        self._read = False
        self._check = False

    def set(self) -> None:
        self._read = True
        super().set()

    def nudge(self) -> None:
        super().set()

    def check(self) -> None:
        """End the wait and read the project now if it moved since the last read (the idle fallback's question)."""
        self._check = True
        super().set()

    def take(self) -> bool:
        """End this wake; whether a full read was asked since the last take."""
        super().clear()
        read, self._read = self._read, False
        return read

    def take_check(self) -> bool:
        """Whether a moved-since-read check was asked since the last take."""
        check, self._check = self._check, False
        return check


@dataclass
class ProjectRuntime:
    runtime_id: str
    project_id: str
    project_dir: str
    operations: OperationManager
    binding: ProjectBinding
    state: str = "open"
    retained: dict | None = None
    projection: str = "unknown"
    error: HubError | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    refresh_lock: threading.RLock = field(default_factory=threading.RLock)
    wake: _Wake = field(default_factory=_Wake)
    thread: threading.Thread | None = None
    last_workers: tuple = ()
    last_snapshot: dict | None = None
    projection_key: tuple | None = None
    # Keyed by the copy's origin page identity, which never moves. Runtime
    # lifetime only: the copies themselves are the project's, and which ones
    # exist is derived from it, never remembered here.
    work_copies: dict[tuple[str, str, str | None], _WorkCopyObservation] = field(default_factory=dict)
    # A project whose documents will not list at all. Separate from ``error``
    # because it is not a fact about the retained projection.
    work_copy_error: HubError | None = None
    # What the last successful derivation of ``work_copies`` was read from.
    work_copy_key: tuple | None = None
    next_working_cleanup: float = 0.0
    # ``binding_signature`` when the binding was last verified. ``get`` checks
    # the project again only once these two stats move (#363).
    binding_signature: tuple | None = None


class ProjectRuntimeManager:
    def __init__(self, applications, chats):
        self.applications, self.chats = applications, chats
        self.server_id = str(uuid4())
        self.events = StudioEvents(buffer_size=256)
        self._lock = threading.RLock()
        self._projects: dict[str, ProjectRuntime] = {}
        self._closing = threading.Event()
        self._client_count = 0
        self._chat_changed = set()
        # One attachment per project to its worker's event stream (#366).
        self._worker_events: dict[str, _WorkerEvents] = {}
        # Each project's latest Studio events, which open a new page's event panel (#366).
        self._studio_replay: dict[str, deque] = {}
        # A supervisor that owns real workers says when one changed, so an idle
        # observer reports a crash at once instead of on its next pass (#435).
        supervisor = getattr(applications, "supervisor", None)
        if supervisor is not None:
            supervisor.add_listener(self._nudge)
        if chats is not None:
            # An external chat's result card is built from these records (#404 F15).
            chats.turn_results = self.turn_results

    def turn_results(self, session_id: str, project_dir: str, since: str) -> list[tuple[str, str, str]]:
        """The candidates a chat's own requests made since ``since``: (candidateId, request kind, admitted at).

        Only runs this Hub admitted for that chat and saw complete; an acceptance
        names no new run. A run still under way is read once more first. Called
        outside the chat lock.
        """
        key = project_key(project_dir)
        with self._lock:
            runtime = next((row for row in self._projects.values() if project_key(row.project_dir) == key), None)
        if runtime is None:
            return []
        start = datetime.fromisoformat(since)

        if any(record.status in _ACTIVE for record in runtime.operations.runs_of(session_id, start)) and runtime.state == "open":
            with suppress(HubFailure, StudioError, OSError, TimeoutError, HTTPException):
                self.refresh(runtime)
        return [(record.candidateId, record.kind, record.createdAt) for record in runtime.operations.runs_of(session_id, start)
                if record.status == "completed"]

    @property
    def _clients(self) -> int:
        return self._client_count

    @_clients.setter
    def _clients(self, value: int) -> None:
        # Every project's status carries the attached client count.
        self._client_count = value
        self._nudge()

    def _nudge(self) -> None:
        """End every observer's wait for a look at its status values, without a read."""
        with self._lock:
            runtimes = tuple(self._projects.values())
        for runtime in runtimes:
            runtime.wake.nudge()

    def emit(self, kind: str, runtime_id: str | None = None):
        self.events.publish(event={"kind": kind, "runtimeId": runtime_id})

    def index_hint(self, runtime_id: str, index: dict | None) -> None:
        """Tell attached clients that a project's index moved (``index``), or may have (None): read it again."""

        self.events.publish(event={"kind": "index/committed", "runtimeId": runtime_id, "index": index})

    def studio_event(self, runtime_id: str, stream: str | None, event: dict) -> None:
        """Relay one of a project worker's own events, numbered on its ``stream``, and keep it for replay."""

        relayed = {"kind": "studio/event", "runtimeId": runtime_id, "stream": stream, "studio": event}
        with self._lock:
            self._studio_replay.setdefault(runtime_id, deque(maxlen=_STUDIO_REPLAY)).append(relayed)
        self.events.publish(event=relayed)

    def studio_replay(self) -> list[dict]:
        """Every project's kept Studio events, oldest first per project."""

        with self._lock:
            return [event for kept in self._studio_replay.values() for event in kept]

    def _follow_worker(self, runtime: ProjectRuntime, workers) -> None:
        """Keep one attachment to the running worker's event stream, and none to any other."""

        url = None
        if runtime.state == "open" and not self._closing.is_set():
            url = next((row.url for row in workers
                        if row.state in {"ready", "busy"} and row.healthy and row.url), None)
        with self._lock:
            current = self._worker_events.get(runtime.runtime_id)
            if current is not None and current.url == url and current.alive:
                return
            if current is not None:
                current.stop()
                del self._worker_events[runtime.runtime_id]
            if url is None:
                return
            follower = _WorkerEvents(self, runtime.runtime_id, url)
            self._worker_events[runtime.runtime_id] = follower
        follower.start()

    def _stop_following(self, runtime_id: str | None = None) -> None:
        with self._lock:
            ids = list(self._worker_events) if runtime_id is None else [runtime_id]
            followers = [self._worker_events.pop(key) for key in ids if key in self._worker_events]
            for key in ids:
                self._studio_replay.pop(key, None)
        for follower in followers:
            follower.stop()

    def chat_changed(self, session):
        # Called under ChatStore's lock: no lock inversion, IO or project open.
        with self._lock:
            self._chat_changed.add(project_key(session.projectDir))
        self._nudge()

    def open(self, project_id: str, project_dir: str) -> ProjectRuntime:
        # Taken before the check, so a change during it is seen by the next get().
        signature = binding_signature(project_dir)
        key = project_key(project_dir)
        runtime_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{key}"))
        with self._lock:
            attached = self._projects.get(runtime_id)
        # An attached runtime whose folder has the same two stats is the project
        # it was verified to be, as in get(): a page opening it again (a reload)
        # does not open and verify the whole project again (#449).
        if (attached is None or signature is None or signature != attached.binding_signature
                or project_key(attached.project_dir) != key):
            actual_id, actual_dir = _project(project_dir)
            if actual_id != project_id:
                raise HubFailure(409, "PROJECT_MISMATCH", "The requested project identity does not match this folder.")
            key = project_key(actual_dir)
            runtime_id = str(uuid5(NAMESPACE_URL, f"{actual_id}:{key}"))
        else:
            actual_id, actual_dir = attached.project_id, attached.project_dir
        with self._lock:
            if self._closing.is_set():
                raise HubFailure(409, "HUB_STOPPING", "Hub is closing.")
            runtime = self._projects.get(runtime_id)
            if runtime is None:
                binding = ProjectBinding.unconfigured(Path(actual_dir), project_id=actual_id)
                operations = OperationManager(actual_id, project_dir=actual_dir,
                    journal_path=self.applications.runtime_root / "runtime/operations" / f"{runtime_id}.json")
                runtime = ProjectRuntime(runtime_id, actual_id, actual_dir, operations, binding)
                self._projects[runtime_id] = runtime
            runtime.binding_signature = signature
            if runtime.state == "closed":
                runtime.state = "open"
            if runtime.thread is None or not runtime.thread.is_alive():
                runtime.thread = threading.Thread(target=self._watch, args=(runtime,), daemon=True, name=f"hub-project-{project_id}")
                runtime.thread.start()
                self.emit("project/opened", runtime_id)
                runtime.wake.set()
            else:
                # Already observed: read the project again now only if something
                # on disk moved since the last read, as the idle fallback does,
                # instead of a full read while the page that opened it loads.
                runtime.wake.check()
            return runtime

    def get(self, runtime_id: str, project_id: str | None = None) -> ProjectRuntime:
        with self._lock:
            runtime = self._projects.get(runtime_id)
        if runtime is None:
            raise HubFailure(404, "RUNTIME_NOT_FOUND", "Open the project's runtime first.")
        if project_id is not None and project_id != runtime.project_id:
            raise HubFailure(409, "PROJECT_MISMATCH", "This request names another project.")
        # Opening and verifying the project costs tens of milliseconds, and every
        # proxied request comes through here. Two stats say whether it can have
        # changed; only then is it checked again, with the same refusal.
        signature = binding_signature(runtime.project_dir)
        if signature is None or signature != runtime.binding_signature:
            if _project(runtime.project_dir) != (runtime.project_id, runtime.project_dir):
                raise HubFailure(409, "PROJECT_MISMATCH", "The runtime's project binding changed.")
            runtime.binding_signature = signature
        return runtime

    def discover(self):
        for project in self.chats.projects():
            runtime_id = str(uuid5(NAMESPACE_URL, f"{project.projectId}:{project_key(project.projectDir)}"))
            with self._lock:
                present = runtime_id in self._projects
            if not present:
                try:
                    self.open(project.projectId, project.projectDir)
                except (HubFailure, StudioError):
                    continue

    def project_snapshot(self, runtime: ProjectRuntime, *, chats: list | None = None,
                         workers: tuple | None = None, keys: dict[str, str] | None = None) -> ProjectRuntimeDto:
        """One project's status. ``snapshot`` passes the chats and workers it read
        once for every project, and ``keys``, each path it has resolved already:
        resolving a path is a file-system call, and it was most of a snapshot (#363).
        """

        keys = {} if keys is None else keys

        def key_of(path: str) -> str:
            if path not in keys:
                keys[path] = project_key(path)
            return keys[path]

        key = key_of(runtime.project_dir)
        if chats is None:
            chats = [*self.chats.list(), *self.chats.list(archived=True)]
        sessions = [row for row in chats if row.projectId == runtime.project_id and key_of(row.projectDir) == key]
        if workers is None:
            rows = self.applications.worker_snapshots(project_dir=runtime.project_dir)
        else:
            # The supervisor's own selection: a worker whose launch names this project.
            rows = [row for row in workers if row.project_dir is not None and key_of(row.project_dir) == key]
        workers = [worker_dto(row) for row in rows]
        with runtime.lock:
            # A refused work-copy edit is reported through the runtime's one
            # error surface, behind anything wrong with the project itself and
            # one at a time: the point is that the architect learns their save
            # did not land, not that every copy gets its own channel.
            failure = runtime.error or runtime.work_copy_error or next(
                (row.failure for row in tuple(runtime.work_copies.values()) if row.failure is not None), None)
            return ProjectRuntimeDto(runtimeId=runtime.runtime_id, projectId=runtime.project_id, projectDir=runtime.project_dir,
                state=runtime.state, workers=workers, operations=runtime.operations.records(), sessions=sessions,
                retained=runtime.retained, projection=runtime.projection, clients=self._clients, error=failure)

    def snapshot(self) -> HubRuntimeDto:
        with self._lock:
            runtimes = tuple(self._projects.values())
        buffered = self.events.replay()
        # One read of the chats and of the workers, and one resolution of each
        # path, answer every project; per project each was read again.
        keys: dict[str, str] = {}
        chats = [*self.chats.list(), *self.chats.list(archived=True)]
        workers = self.applications.worker_snapshots()
        return HubRuntimeDto(serverId=self.server_id, sequence=buffered[-1]["seq"] if buffered else 0,
            projects=[self.project_snapshot(runtime, chats=chats, workers=workers, keys=keys) for runtime in runtimes],
            workers=[worker_dto(row) for row in workers])

    def _read_retained(self, runtime: ProjectRuntime, *, worker=None) -> dict:
        from project_runtime.status import inspect_runtime
        from project_runtime.api.dto.runtime import runtime_dto
        ids = runtime.operations.candidate_ids()
        run_ids = runtime.binding.run_ids()
        candidates = {}
        if worker is not None:
            # Preserve the existing recent window and tracked operations, but
            # never make one HTTP request parse fifty large retained results.
            # A page discovers ordinary runs without misclassifying them as
            # explicit candidates. Every refresh revalidates its evidence.
            recent = tuple(reversed(run_ids[-50:]))
            reads = [(offset, (run_id,) if run_id in ids else ())
                     for offset, run_id in enumerate(recent)]
            reads.extend((len(run_ids), (candidate_id,)) for candidate_id in ids if candidate_id not in recent)
            reads = reads or [(0, ())]
        else:
            # In-process cold/recovery reads have no HTTP deadline. They use
            # the same inspector and never trust a previous completed row.
            reads = [(0, ids[offset:offset + 200]) for offset in range(0, len(ids), 200)] or [(0, ())]
        result = None
        scanned = 0
        for offset, chunk in reads:
            if worker is None:
                current = runtime_dto(inspect_runtime(runtime.binding, run_ids=chunk)).model_dump(by_alias=True)
            else:
                query = urlencode([("limit", "1"), ("offset", str(offset)), *(("candidateId", value) for value in chunk)])
                response = request_http(worker.url, f"/api/runtime?{query}", timeout=10)
                if response.status != 200:
                    raise HubFailure(503, "RUNTIME_READ_FAILED", "The bound Studio could not read its runtime state.")
                current = response.json()
            if current.get("projectId") != runtime.project_id or project_key(current.get("projectDir", "")) != project_key(runtime.project_dir):
                raise HubFailure(409, "PROJECT_MISMATCH", "The runtime snapshot belongs to another project.")
            if result is not None and (result.get("published"), result.get("branches")) != (current.get("published"), current.get("branches")):
                raise HubFailure(409, "RUNTIME_CHANGED", "The project changed during runtime inspection; read its next snapshot.")
            candidates.update((row["candidateId"], row) for row in current.get("candidates", []))
            scanned += current.get("runsScanned", 0)
            result = current
        if runtime.binding.run_ids() != run_ids:
            raise HubFailure(409, "RUNTIME_CHANGED", "Project runs changed during runtime inspection; read its next snapshot.")
        result["candidates"] = list(candidates.values())
        result["runsScanned"] = scanned
        result["hasMore"] = len(run_ids) > 50
        return result

    def refresh(self, runtime: ProjectRuntime, *, cold: bool = False) -> None:
        with runtime.refresh_lock:
            self._refresh(runtime, cold=cold)

    def _refresh(self, runtime: ProjectRuntime, *, cold: bool = False) -> None:
        workers = self.applications.worker_snapshots(project_dir=runtime.project_dir)
        worker = next(iter(workers), None)
        alive = worker is not None and worker.state in {"ready", "busy"} and worker.healthy
        previous_workers = runtime.last_workers
        current_workers = tuple((row.instance_id, row.state, row.healthy) for row in workers)
        if current_workers != previous_workers:
            runtime.last_workers = current_workers
            for row in workers:
                self.emit(f"worker/{row.state}", runtime.runtime_id)
            cold = cold or not alive
        if alive and not cold:
            retained = self._read_retained(runtime, worker=worker)
        elif cold or runtime.retained is None:
            retained = self._read_retained(runtime)
        else:
            return
        latest = next(iter(self.applications.worker_snapshots(project_dir=runtime.project_dir)), None)
        if worker is not None and (latest is None or latest.instance_id != worker.instance_id or latest.state not in {"ready", "busy"}):
            alive = False
        if retained.get("projectId") != runtime.project_id or project_key(retained.get("projectDir", "")) != project_key(runtime.project_dir):
            raise HubFailure(409, "PROJECT_MISMATCH", "The runtime snapshot belongs to another project.")
        # A missed health check does not change the projection's source. Keep
        # its binding while stale so the same worker/base can recover without
        # another expensive state read; a new instance or base still rebuilds.
        projection_key = (worker.instance_id, retained.get("published"), retained.get("branches")) if alive else runtime.projection_key
        with runtime.lock:
            previous = runtime.retained
            runtime.retained = retained
            runtime.operations.reconcile(retained, worker_alive=alive)
        if alive and projection_key != runtime.projection_key:
            # A verified candidate remains observed even if the independent
            # default-view rebuild is slow. It does not make that view ready.
            projection = request_http(worker.url, "/api/state", timeout=10)
            if projection.status != 200:
                raise HubFailure(503, "PROJECTION_UNAVAILABLE", "The recovered Studio could not rebuild its retained state projection.")
        with runtime.lock:
            runtime.error = None
            runtime.projection = "ready" if alive else "stale" if worker else "unknown"
            runtime.projection_key = projection_key
        busy = runtime.operations._has_active() or any(row.get("status") in {"queued", "running"} for row in retained.get("jobs", []))
        self.applications.set_busy(project_dir=runtime.project_dir, busy=busy)
        if previous is not None and previous.get("published") != retained.get("published"):
            self.emit("state/committed", runtime.runtime_id)
        if previous is not None and previous.get("branches") != retained.get("branches"):
            self.emit("operation/committed", runtime.runtime_id)

    @staticmethod
    def _work_copy_key(copy: DocumentWorkCopy) -> tuple[str, str, str | None]:
        return copy.run_id, copy.asset_sha256, copy.revision_ref

    def bind_work_copies(self, runtime: ProjectRuntime) -> dict[tuple[str, str, str | None], str]:
        """Re-derive which of this project's registered documents have an editable file.

        The project is the only record of that: a registered document names
        exactly one possible copy path and a row exists only when that file is
        there. So a copy opened before this process started is bound on
        the first pass, and one the architect deleted stops being observed. No
        directory is scanned, no name is guessed and nothing is materialised —
        a copy exists because somebody asked the Studio for it.
        """

        copies = {self._work_copy_key(copy): copy for copy in list_document_work_copies(runtime.binding)}
        for key in tuple(runtime.work_copies):
            if key not in copies:
                del runtime.work_copies[key]
        for key, copy in copies.items():
            observed = runtime.work_copies.get(key)
            if observed is None:
                runtime.work_copies[key] = _WorkCopyObservation(copy)
            else:
                if copy.refusal != observed.copy.refusal:
                    # A settled file otherwise waits for its content refresh.
                    # Whether the owner will take these bytes has just changed,
                    # so the next pass has to look: an edit refused while the
                    # document was someone else's is still waiting to register.
                    observed.hashed_at_ns = None
                observed.copy = copy
                if (observed.failure is not None
                        and observed.failure.code == "WORK_COPY_OPERATION_INTERRUPTED"
                        and observed.observed_sha256 == copy.head_asset_sha256):
                    # A lost response is not a failed write. Verify the exact
                    # retained successor through the owner's reader, without
                    # replaying the POST or inferring success from liveness.
                    try:
                        document, _ = document_bytes(runtime.binding, copy.head_run_id,
                                                     copy.head_asset_sha256, copy.head_revision_ref)
                    except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError):
                        continue
                    if (document.run_id, document.asset_sha256, document.revision_ref) == (
                            copy.head_run_id, copy.head_asset_sha256, copy.head_revision_ref):
                        observed.failure = None
                        self.emit("artifact/updated", runtime.runtime_id)
        return {key: str(copy.path) for key, copy in copies.items()}

    def _work_copy_inputs(self, runtime: ProjectRuntime) -> tuple:
        """Everything that decides which work copies exist, without deriving them.

        Deriving them lists the project's documents, which reads every record
        of every run: seconds, and most of the project's bytes, on a large
        project (#314). The rows depend on nothing else than the runs, their
        document records and which files each run's copy workspace holds.
        A record is named by its content digest, so a changed record is a
        changed ref; this reads those refs and the names of the copy files and
        nothing else. It only says when to derive again: the derivation stays
        the one answer to which copies exist.
        """

        binding, inputs = runtime.binding, []
        for run_id in binding.run_ids():
            records = tuple(ref.uri for kind in _DOCUMENT_RECORD_KINDS
                            for ref in binding.record_refs(run_id, kind=kind))
            workspace = binding.repository.layout.run(run_id).workspaces / WORK_COPY_WORKSPACE
            files = tuple(sorted(str(Path(folder, name)) for folder, _, names in os.walk(workspace) for name in names))
            inputs.append((run_id, records, files))
        return tuple(inputs)

    def _stable_work_copy_bytes(self, observed: _WorkCopyObservation) -> bytes | None:
        """The copy's settled contents, or ``None`` while it is still moving.

        A producer's save is not atomic on every path. The same
        file identity, size and mtime must remain stable for the settle interval,
        and the complete sample is checked again after reading. Identity catches
        atomic replacements even when timestamps are preserved; a bounded content
        refresh catches same-size in-place writes that preserve timestamps too.
        The document owner still validates complete bytes before registration.
        """

        path = observed.copy.path
        try:
            before = path.stat()
        except FileNotFoundError:
            # A transient missing file is what an atomic save/rename looks
            # like from here, not a deletion. Binding decides what exists.
            return None
        sample = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        now = time.monotonic_ns()
        if observed.sample != sample:
            observed.sample, observed.hashed_at_ns = sample, None
            observed.sample_since_ns = now
            return None
        if observed.sample_since_ns is None or now - observed.sample_since_ns < _WORK_COPY_SETTLED_NS:
            return None
        if (observed.hashed_at_ns is not None
                and now - observed.hashed_at_ns < _WORK_COPY_CONTENT_REFRESH_NS):
            return None
        try:
            data = path.read_bytes()
            after = path.stat()
        except FileNotFoundError:
            return None
        after_sample = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if after_sample != sample:
            observed.sample, observed.hashed_at_ns = after_sample, None
            observed.sample_since_ns = time.monotonic_ns()
            return None
        observed.hashed_at_ns = time.monotonic_ns()
        return data

    def _observe_work_copies(self, runtime: ProjectRuntime) -> int:
        """Register what each settled work copy changed to, whole, or say why not.

        Three cases are kept apart on purpose. Bytes this process has not seen
        move are nothing, even when the document they answer for was replaced
        by some other client — an untouched copy must never roll the Board back.
        Bytes that moved to what the project already says are equally nothing.
        Anything else moved, including an undo to an earlier registration, and
        is offered to the document owner: it either becomes the next registered
        revision or its refusal is carried to the user, per copy, unchanged.

        A copy the document owner no longer considers editable is never dropped:
        its file is still on disk and still holds someone's work, so unwatching
        it would throw away every save made from here on. It is reported only
        when its bytes have actually moved — an untouched copy whose document
        was replaced elsewhere is news the row carries, not a project error —
        and a refused edit keeps its place in the queue until the refusal goes.
        """

        registered = 0
        for key, observed in tuple(runtime.work_copies.items()):
            try:
                data = self._stable_work_copy_bytes(observed)
            except OSError as exc:
                observed.failure = HubError(code="WORK_COPY_READ_FAILED",
                    detail=f"{observed.copy.file_name}: {exc}"[:1200])
                continue
            if data is None:
                continue
            digest = hashlib.sha256(data).hexdigest()
            baseline = observed.observed_sha256
            if digest == baseline:
                # Nothing has moved since the last look, so neither a read that
                # failed nor a refusal is waiting on anything any more.
                if observed.failure and observed.failure.code in {
                        "WORK_COPY_READ_FAILED", "WORK_COPY_NOT_EDITABLE"}:
                    observed.failure = None
                continue
            # Binding re-derives these rows, but only on a due pass, so the row
            # this observation carries can be a whole idle interval old. The
            # derivation below is what makes the answer current: aim at the
            # document the project answers for right now, or say why it cannot.
            try:
                copy = next((row for row in list_document_work_copies(runtime.binding)
                             if self._work_copy_key(row) == key), None)
            except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
                observed.hashed_at_ns = None
                observed.failure = HubError(code="WORK_COPY_READ_FAILED",
                    detail=f"{observed.copy.file_name}: {exc}"[:1200])
                continue
            if copy is None:
                continue
            observed.copy = copy
            if copy.refusal is not None:
                if baseline is None and digest in copy.known_sha256:
                    # Untouched since it was seeded. The document it answered
                    # for moved on without it, which is not this copy's news to
                    # report: the row carries the reason, and nothing is stale.
                    observed.failure = None
                    continue
                # These bytes are an edit the owner will not take. Say so, and
                # leave the baseline alone: when the refusal goes away the same
                # edit is still waiting to be offered, not silently swallowed.
                observed.failure = HubError(code="WORK_COPY_NOT_EDITABLE",
                    detail=f"{copy.file_name}: {copy.refusal}"[:1200])
                continue
            observed.observed_sha256 = digest
            if digest == copy.head_asset_sha256:
                # The copy agrees with the document again. Whatever was refused
                # before is no longer waiting on anything, so it stops being
                # reported without anything having been registered.
                observed.failure = None
                continue
            if baseline is None and digest in copy.known_sha256:
                # With no prior observation, old untouched bytes and an offline
                # undo cannot be distinguished. Keep the registered page and
                # expose the disagreement instead of silently losing the edit.
                observed.failure = HubError(code="WORK_COPY_RESTART_CONFLICT", detail=(
                    f"{copy.file_name}: The work copy matches an earlier page version. "
                    "Hub cannot tell whether it was left unchanged or reverted while closed. "
                    "The current registered page is still shown; review the copy before editing again."
                )[:1200])
                continue
            try:
                if digest in copy.known_sha256:
                    raise StudioError(409, "DOCUMENT_REVISION_ALREADY_REGISTERED",
                        "These bytes are already a registered revision of this page. "
                        "Returning to that version cannot be registered as a new revision; "
                        "the current registered page is still shown.")
                # Board uploads and observed edits must share Studio's document
                # writer. Calling save_document in this Hub process would use
                # a separate lock and could create two successors of one page.
                self.service(runtime)
                result = self.forward(runtime, "/api/documents", "POST", json.dumps({
                    "projectId": runtime.project_id, "runId": None,
                    "fileName": copy.file_name, "mimeType": copy.mime_type,
                    "contentBase64": base64.b64encode(data).decode("ascii"),
                    # One file standing for one whole document: say that, and
                    # let the document owner refuse a file that has gained or
                    # lost a page rather than infer the intent from a page list.
                    "replacesDocument": {"runId": copy.head_run_id,
                        "assetSha256": copy.head_asset_sha256, "revisionRef": copy.head_revision_ref},
                }).encode("utf-8"), {"content-type": "application/json"})
                if result.status >= 400:
                    error = result.json()
                    raise StudioError(result.status, error.get("code", "DOCUMENT_WORK_COPY_FAILED"),
                                      error.get("detail", "The edited page was not registered."))
            except HubFailure as exc:
                if exc.error.code == "WORKER_UNAVAILABLE":
                    # No request was sent; the file stays pending until Studio
                    # is ready. A dispatched request with a lost reply is never retried.
                    observed.observed_sha256 = baseline
                    observed.hashed_at_ns = None
                observed.failure = HubError(code=f"WORK_COPY_{exc.error.code}",
                    detail=f"{copy.file_name}: {exc.error.detail}"[:1200])
                continue
            except StudioError as exc:
                # An edit that cannot be registered is reported, not dropped:
                # the previously registered page stays the Board's preview and
                # this copy carries the reason until it registers something
                # else. One digest, one report — a re-save of the same bytes
                # does not repeat it.
                observed.failure = HubError(code=f"WORK_COPY_{exc.code}",
                                            detail=f"{copy.file_name}: {exc.detail}"[:1200])
                continue
            observed.failure = None
            registered += 1
            self.emit("artifact/updated", runtime.runtime_id)
        return registered

    def _clean_working_draft(self, runtime: ProjectRuntime) -> tuple[str, ...]:
        """The Hub schedules maintenance; only P036 can remove project files.

        What P036 removes is superseded local recovery: crash-recovery copies
        of unsynced edits that nothing reads back. It never removes a run, so
        no operation or conversation has to protect a candidate from it.
        """
        repository = runtime.binding.repository
        if repository.read_working_draft()[1] is None:
            return ()
        return repository.prune_working_draft(now=datetime.now(timezone.utc).isoformat())

    def _watch(self, runtime: ProjectRuntime):
        try:
            self._watch_project(runtime)
        finally:
            self._stop_following(runtime.runtime_id)
            # The binding's layout watch holds the project folder open; a
            # runtime that stopped observing lets go of it, and one opened
            # again watches again on its first read.
            runtime.binding.close()

    def _watch_project(self, runtime: ProjectRuntime):
        next_retained_read = next_work_copy_check = 0.0
        last_snapshot_inputs = last_retained = None
        # The project's read token when the last successful refresh began;
        # None when the watch had published none yet.
        last_token: ReadToken | None = None
        # The same for the last successful derivation of which work copies exist.
        last_copy_token: ReadToken | None = None
        # Taken as observing starts, not on the first idle fallback: the watch
        # walks the project on its own thread while the opening pass reads it,
        # so the reads after that record the token they began under, and the
        # first idle question after an open asks nothing again when nothing
        # moved (#435, #599). Nothing waits for that walk here.
        with suppress(OSError):
            runtime.binding.layout_watch()
        while not self._closing.is_set():
            force_read = runtime.wake.take()
            checked = runtime.wake.take_check()
            workers = self.applications.worker_snapshots(project_dir=runtime.project_dir)
            self._follow_worker(runtime, workers)
            worker_states = tuple((row.instance_id, row.state, row.healthy) for row in workers)
            drained = runtime.state == "closed" and not any(row.process_id is not None for row in workers)
            active = runtime.operations._has_active() or any(
                row.get("status") in {"queued", "running"} for row in (runtime.retained or {}).get("jobs", []))
            prompted = drained or force_read or active or worker_states != runtime.last_workers
            due = prompted or checked or time.monotonic() >= next_retained_read
            try:
                token = None
                if due and not prompted:
                    # Only the idle fallback asks, and it asks only whether a
                    # separate client changed the project. If nothing on disk
                    # moved since the last such read - an equal, stable read
                    # token - the retained history is not read again. The
                    # binding's layout watch keeps that token current on its
                    # own thread, so asking walks nothing (#363).
                    token = runtime.binding.read_token()
                    if last_token is not None and last_token.stable and token == last_token:
                        due = False
                        next_retained_read = time.monotonic() + _IDLE_RETAINED_REFRESH_S
                if due:
                    # Keep liveness/session reads responsive without rebuilding
                    # unchanged retained history on every idle heartbeat. Hub
                    # mutations wake this observer; the fallback sees changes
                    # made through a separate Studio/project client.
                    last_token = None
                    if token is None:
                        # Every read records the token it began under, so the
                        # first idle fallback after an open or a wake is skipped
                        # too when nothing moved since. Not waited for: before
                        # the watch's first walk there is none, and the next
                        # idle fallback reads as it always did (#435).
                        token = runtime.binding.read_token(wait=False)
                    self.refresh(runtime, cold=drained)
                    next_retained_read = time.monotonic() + _IDLE_RETAINED_REFRESH_S
                    # Taken before the read began, and kept only once it succeeded.
                    last_token = token
            except (HubFailure, StudioError, OSError, HTTPException, ValueError) as exc:
                next_retained_read = time.monotonic() + _IDLE_RETAINED_REFRESH_S
                with runtime.lock:
                    runtime.error = exc.error if isinstance(exc, HubFailure) else HubError(code="RUNTIME_READ_FAILED", detail=str(exc)[:1200])
                    runtime.projection = "stale"
            # Work copies are an explicit opt-in beside the project, not part
            # of its retained projection. A document listing that will not read
            # or a file that will not open therefore says nothing about whether
            # the project is stale, and never delays the next retained read.
            try:
                # Which copies exist is derived again on a Hub mutation's wake
                # and on a drained runtime's last pass. A check and the idle
                # cadence first ask the project's read token, as the retained
                # read does: deriving lists the document records of every run
                # (#599), and an equal, stable token says that nothing deciding
                # the copies moved since the last derivation. Their metadata is
                # still watched every heartbeat, with bounded content reads
                # after a copy has settled.
                derive, copy_token = False, None
                if force_read or drained or checked or time.monotonic() >= next_work_copy_check:
                    next_work_copy_check = time.monotonic() + _WORK_COPY_CHECK_S
                    if force_read or drained:
                        # As for the retained read, not waited for (#435).
                        derive, copy_token = True, runtime.binding.read_token(wait=False)
                    else:
                        copy_token = runtime.binding.read_token()
                        derive = not (last_copy_token is not None and last_copy_token.stable
                                      and copy_token == last_copy_token)
                if derive:
                    last_copy_token = None
                    inputs = self._work_copy_inputs(runtime)
                    if inputs != runtime.work_copy_key:
                        self.bind_work_copies(runtime)
                        runtime.work_copy_key = inputs
                    # Taken before the derivation began, and kept only once it succeeded.
                    last_copy_token = copy_token
                self._observe_work_copies(runtime)
            except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
                with runtime.lock:
                    runtime.work_copy_error = HubError(code="WORK_COPY_READ_FAILED", detail=str(exc)[:1200])
            else:
                with runtime.lock:
                    runtime.work_copy_error = None
            if time.monotonic() >= runtime.next_working_cleanup:
                runtime.next_working_cleanup = time.monotonic() + _WORKING_CLEANUP_INTERVAL_S
                try:
                    # No client lists superseded recovery, so removing it is
                    # not an artifact change and wakes nobody.
                    self._clean_working_draft(runtime)
                except (ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
                    # An inconsistent recovery snapshot refuses expiry while the
                    # existing project remains available for inspection.
                    with runtime.lock:
                        runtime.error = HubError(code="WORKING_CLEANUP_REFUSED", detail=str(exc)[:1200])
            with self._lock:
                chat_changed = project_key(runtime.project_dir) in self._chat_changed
                self._chat_changed.discard(project_key(runtime.project_dir))
            with runtime.lock:
                retained = runtime.retained
                snapshot_inputs = (workers, runtime.state, runtime.projection, self._clients,
                    runtime.error, runtime.work_copy_error,
                    tuple(row.failure for row in runtime.work_copies.values()))
            # Active work and wakes still read the complete view. Idle ticks
            # compare only small status values; retained refresh replaces its
            # value, and the existing chat callback reports session changes.
            # Work-copy refusals and worker details can change between reads.
            if (due or chat_changed or snapshot_inputs != last_snapshot_inputs
                    or retained is not last_retained):
                last_snapshot_inputs, last_retained = snapshot_inputs, retained
                snapshot = self.project_snapshot(runtime).model_dump()
                if snapshot != runtime.last_snapshot or chat_changed:
                    previous = runtime.last_snapshot
                    runtime.last_snapshot = snapshot
                    self.emit("agent/progress" if chat_changed else "project/updated", runtime.runtime_id)
                    if previous and previous.get("projection") != snapshot["projection"]:
                        self.emit("projection/updated" if snapshot["projection"] == "ready" else "projection/invalidated", runtime.runtime_id)
            if drained:
                break
            settled = (not active and not runtime.work_copies and runtime.state == "open"
                       and all(row.state in _SETTLED_WORKER_STATES for row in workers))
            runtime.wake.wait(_IDLE_HEARTBEAT_S if settled else _ACTIVE_HEARTBEAT_S)

    def service(self, runtime: ProjectRuntime):
        self.get(runtime.runtime_id)
        if runtime.state != "open":
            raise HubFailure(409, "RUNTIME_CLOSED", "This project runtime is closed.")
        worker = next(iter(self.applications.worker_snapshots(project_dir=runtime.project_dir)), None)
        if worker is None or worker.state not in {"ready", "busy"} or not worker.healthy or not worker.url:
            raise HubFailure(503, "WORKER_UNAVAILABLE", "The project's Studio is not ready. Recover a crashed worker explicitly before continuing.")
        return worker

    def forward(self, runtime: ProjectRuntime, path: str, method: str, body: bytes,
                headers: dict[str, str]) -> HttpResult:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or not (parsed.path.startswith("/api/") or parsed.path == "/openapi.json") or ".." in parsed.path.split("/"):
            raise HubFailure(422, "STUDIO_PATH_INVALID", "Only the bound Studio API can be called.")
        for key, values in parse_qs(parsed.query).items():
            if key in {"projectId", "project_id"} and values != [runtime.project_id]:
                raise HubFailure(409, "PROJECT_MISMATCH", "This request names another project.")
        try:
            payload = json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("projectId", runtime.project_id) != runtime.project_id:
            raise HubFailure(409, "PROJECT_MISMATCH", "This request names another project.")
        mutation = method not in {"GET", "HEAD", "OPTIONS"} and not parsed.path.startswith("/api/events/") and parsed.path not in {"/api/state/closure", "/api/pick/resolve"}
        if method == "POST" and parsed.path == "/api/drawing-recipes/inspect":
            mutation = False  # File validation is a read, including a refused file.
        admission = None
        if mutation:
            operation_id = headers.get("idempotency-key") or str(uuid4())
            session_id = headers.get("x-monkey-chat")
            if session_id:
                session = self.chats.get(session_id)
                if session.projectId != runtime.project_id or project_key(session.projectDir) != project_key(runtime.project_dir):
                    raise HubFailure(409, "PROJECT_MISMATCH", "This chat is bound to another project.")
                if session.status != "running":
                    raise HubFailure(409, "CHAT_NOT_RUNNING", "This chat is no longer running.")
                if method == "POST" and parsed.path == "/api/admissions" and isinstance(payload, dict):
                    # #404 F13: the Agent withdraws only a result this chat asked this Hub for.
                    withdrawn = [row.get("runId") for row in payload.get("results") or () if isinstance(row, dict) and row.get("outcome") == "withdrawn"]
                    foreign = [run_id for run_id in withdrawn if not runtime.operations.made_by(session_id, run_id)]
                    if foreign:
                        raise HubFailure(409, "CANDIDATE_NOT_THIS_CHATS", "The Agent withdraws only a result it made in this chat: "
                                         + ", ".join(map(str, foreign)) + " was not. Only the architect's words can reject another result.")
            admission, fresh = runtime.operations.admit(operation_id, method, path, body,
                retained=runtime.retained, source="chat" if session_id else "studio", session_id=session_id)
            if not fresh:
                if not admission.finished.wait(10):
                    raise HubFailure(409, "OPERATION_RUNNING", "The same operation is still running. Read its runtime status; it has not been submitted again.")
                if admission.response is not None:
                    return admission.response
                raise HubFailure(409, "OPERATION_NEEDS_RECOVERY", "The operation lost its response. Read retained runtime results; it has not been replayed.")
            self.emit("operation/started", runtime.runtime_id)
        dispatched = False
        try:
            worker = self.service(runtime)
            forwarded = {key: value for key, value in headers.items()
                         if key in {"content-type", "x-monkey-operation", "x-monkey-parent", "if-none-match",
                                    "x-monkey-turn-id", "x-monkey-parent-span-id"}}
            if admission and method == "POST" and _CANDIDATE_REQUEST.fullmatch(parsed.path):
                candidate_id = admission.record.candidateId
                # A Hub restart can reconstruct this deterministic run identity.
                # An existing run, even incomplete, must never be executed again.
                if candidate_id in runtime.binding.run_ids():
                    raise HubFailure(409, "OPERATION_RETAINED", f"Operation already has retained run {candidate_id}. Read its candidate/runtime status; no work was replayed.")
                forwarded["X-Monkey-Candidate"] = candidate_id
                forwarded["X-Monkey-Worker"] = worker.instance_id
            # The requested route decides this, not the record: a retained
            # refresh also names the candidate's proposal on the acceptance
            # bound to it, and an acceptance path is no proposal to re-read.
            if admission and method == "POST" and _PROPOSAL_CANDIDATE.fullmatch(parsed.path):
                proposal_read = request_http(worker.url, parsed.path.removesuffix("/candidate"), timeout=10)
                if proposal_read.status >= 400:
                    payload = proposal_read.json()
                    raise HubFailure(proposal_read.status, payload.get("code", "PROPOSAL_UNAVAILABLE"), payload.get("detail", "The proposal could not be read."))
                proposal = proposal_read.json()
                source_run = proposal.get("sourceRunId")
                base = runtime.binding.load_run(source_run).base if source_run else runtime.binding.head()
                runtime.operations.bind_proposal(admission, proposal, base.version)
            dispatched = True
            result = request_http(worker.url, path, method, body or None, forwarded, timeout=180)
            if admission:
                result.headers["X-Monkey-Operation-Id"] = admission.record.operationId
                runtime.operations.replied(admission, result)
                self.emit("operation/progress" if admission.record.status in _ACTIVE else f"operation/{admission.record.status}", runtime.runtime_id)
            if mutation:
                # A read changes nothing retained. Waking on every one made each
                # page poll re-read the project's history (#314).
                runtime.wake.set()
            return result
        except (OSError, TimeoutError, HTTPException, HubFailure) as exc:
            if admission:
                if not dispatched and isinstance(exc, HubFailure):
                    # A refusal before dispatch is known, even when a previous
                    # operation left a candidate with this identity on disk.
                    runtime.operations.replied(admission, HttpResult(exc.status, exc.error.model_dump_json().encode("utf-8"), {"content-type": "application/json"}))
                else:
                    runtime.operations.interrupted(admission, "The request did not return a verified result. Read retained state before any new operation; this request will not be replayed.")
                runtime.wake.set()
                self.emit(f"operation/{admission.record.status}" if admission.record.status == "refused" else "operation/failed", runtime.runtime_id)
            if isinstance(exc, HubFailure):
                raise
            raise HubFailure(503, "OPERATION_INTERRUPTED", "The worker connection ended. Runtime status will reconcile retained results; the request was not retried.") from exc

    def recover(self, runtime: ProjectRuntime) -> ProjectRuntimeDto:
        with runtime.refresh_lock:
            if runtime.state != "open":
                raise HubFailure(409, "RUNTIME_CLOSED", "Open this project runtime before recovering its worker.")
            # Inspect while the old process is dead, before any replacement can
            # invalidate a cache. This action never calls a mutation route.
            self.refresh(runtime, cold=True)
            self.applications.recover(project_dir=runtime.project_dir)
            runtime.projection = "rebuilding"
            self.emit("worker/recovering", runtime.runtime_id)
        runtime.wake.set()
        return self.project_snapshot(runtime)

    def acknowledge(self, runtime: ProjectRuntime, operation_id: str) -> OperationRecord:
        """Record that a person read the notice of an operation that failed, went stale or cannot be recovered.

        No request is sent or replayed.
        """
        record = runtime.operations.acknowledge(operation_id)
        self.emit("operation/acknowledged", runtime.runtime_id)
        runtime.wake.set()
        return record

    def close(self, runtime: ProjectRuntime) -> ProjectRuntimeDto:
        runtime.state = "closed"
        self.chats.close_project(runtime.project_dir)
        self.applications.stop("monkeyarch", project_dir=runtime.project_dir)
        runtime.wake.set()
        # Its watcher keeps observing accepted jobs until the owned process
        # finishes normal shutdown, then performs the final retained read.
        self.emit("project/closed", runtime.runtime_id)
        return self.project_snapshot(runtime)

    def begin_shutdown(self):
        self._closing.set()
        self._stop_following()
        with self._lock:
            runtimes = tuple(self._projects.values())
        for runtime in runtimes:
            runtime.wake.set()

    def shutdown(self):
        self.begin_shutdown()
        with self._lock:
            runtimes = tuple(self._projects.values())
        for runtime in runtimes:
            if runtime.thread:
                runtime.thread.join(timeout=12)
            # Its observer closes it on the way out; one that never ran, or
            # has not finished, still lets go of the folder here.
            runtime.binding.close()
