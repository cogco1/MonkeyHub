"""Live project attachments and observed operations over the existing Studio/P036.

No operation is replayed by a watcher or by recovery. A lost HTTP response is
reconciled against retained results; absence of proof remains visible.
"""

from dataclasses import dataclass, field
from http.client import HTTPException
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow_studio_api.application.artifacts import (
    DocumentWorkCopy,
    list_document_work_copies,
)
from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.application.events import StudioEvents
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .chat import _NoRedirect, _project
from .models import HubError, HubFailure
from .runtime_models import HubRuntimeDto, OperationRecord, ProjectRuntimeDto, WorkerStatus


_CANDIDATE_REQUEST = re.compile(
    r"^/api/(proposals/[^/]+/candidate|candidates/combine|capabilities/[^/]+/run|options/[^/]+/select|program)$"
)
_ACCEPT_REQUEST = re.compile(r"^/api/candidates/([^/]+)/accept$")
_PROPOSAL_CANDIDATE = re.compile(r"^/api/proposals/([^/]+)/candidate$")
_ACTIVE = {"queued", "planning", "validated", "executing", "committing"}
_IDLE_RETAINED_REFRESH_S = 30
# How long a work copy's last write must predate the read that hashed it before
# an unchanged stat sample is believed without reading again. Filesystem
# timestamps are coarse: two rewrites inside one clock tick share an
# ``st_mtime_ns``, so a same-size rewrite landing in the tick the digest was
# taken in would otherwise be invisible. Beyond this margin it cannot be, and a
# settled copy stops being read on every heartbeat.
_WORK_COPY_SETTLED_NS = 2_000_000_000


def project_key(path: str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def worker_dto(row) -> WorkerStatus:
    return WorkerStatus(
        workerId=row.worker_id, serviceId=row.service_id, projectId=row.project_id,
        projectDir=row.project_dir, instanceId=row.instance_id, processId=row.process_id,
        desiredState=row.desired_state, state=row.state, healthy=row.healthy,
        url=row.url, error=row.error,
    )


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self):
        try:
            value = json.loads(self.body)
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeError):
            return {}


def request_http(base: str, path: str, method="GET", body: bytes | None = None,
                 headers: dict[str, str] | None = None, *, timeout: float = 10) -> HttpResult:
    """Exactly one request, including error responses. Never follows a redirect."""
    request = Request(base.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        response = build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return HttpResult(response.status, response.read(), {
            name: value for name, value in response.headers.items()
            if name.lower() in {"content-type", "content-disposition", "etag", "cache-control"}
        })


@dataclass
class _Admission:
    record: OperationRecord
    signature: tuple[str, str, str]
    expected_stage: str | None = None
    branch_id: str = "main"
    accepting_candidate: bool = False
    response: HttpResult | None = None
    finished: threading.Event = field(default_factory=threading.Event)


class OperationManager:
    """Request admission and recovery observations, never a project writer."""

    def __init__(self, project_id: str, *, journal_path: Path | None = None,
                 project_dir: str | None = None):
        self.project_id = project_id
        self.journal_path = journal_path
        self.project_dir = project_key(project_dir) if project_dir is not None else None
        if journal_path is not None and self.project_dir is None:
            raise ValueError("A durable operation manager requires its exact project directory.")
        self._lock = threading.RLock()
        self._operations: dict[str, _Admission] = {}
        self._retained: dict[str, OperationRecord] = {}
        self._restore()

    def _restore(self) -> None:
        if self.journal_path is None or not self.journal_path.exists():
            return
        try:
            saved = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if saved["projectId"] != self.project_id or saved["projectDir"] != self.project_dir:
                raise ValueError("The operation journal belongs to another project binding.")
            for row in saved["operations"]:
                record = OperationRecord.model_validate(row["record"])
                signature = row["signature"]
                if (record.projectId != self.project_id or str(UUID(record.operationId)) != record.operationId
                        or record.operationId in self._operations
                        or not isinstance(signature, list) or len(signature) != 3
                        or not all(isinstance(value, str) for value in signature)
                        or not re.fullmatch(r"[0-9a-f]{64}", signature[2])
                        or not isinstance(row["acceptingCandidate"], bool)
                        or not isinstance(row["branchId"], str)
                        or (row["expectedStage"] is not None and not isinstance(row["expectedStage"], str))):
                    raise ValueError("Invalid saved operation binding.")
                # A local journal never proves a successful P036 commit/result.
                record.committed, record.resultDigest, record.resultRevision = False, None, None
                if record.status in _ACTIVE or (record.candidateId and record.status == "completed"):
                    record.status = "needs_recovery"
                    record.reason = "Hub restarted before this operation's retained result was reconciled. No request was replayed."
                admission = _Admission(record, tuple(signature), row["expectedStage"],
                                       row["branchId"], row["acceptingCandidate"])
                admission.finished.set()  # A prior process cannot deliver its HTTP response.
                self._operations[record.operationId] = admission
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HubFailure(503, "OPERATION_LOG_INVALID", "The saved operation identities could not be read for this project. Requests were not replayed.") from exc

    def _save(self) -> None:
        if self.journal_path is None:
            return
        # Only recovery metadata crosses this Hub-runtime boundary. Request
        # bodies and successful project results stay with their existing owners.
        saved = {"projectId": self.project_id, "projectDir": self.project_dir, "operations": [{
            "record": row.record.model_dump(exclude={"committed", "resultDigest", "resultRevision", "reason"}),
            "signature": row.signature, "expectedStage": row.expected_stage,
            "branchId": row.branch_id, "acceptingCandidate": row.accepting_candidate,
        } for row in self._operations.values()]}
        temporary = self.journal_path.with_suffix(".tmp")
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(saved, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.journal_path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise HubFailure(503, "OPERATION_LOG_UNAVAILABLE", "The operation identity could not be saved. Read runtime status before any new request; this request was not retried.") from exc

    def admit(self, operation_id: str, method: str, path: str, body: bytes, *,
              retained: dict | None, source: str, session_id: str | None) -> tuple[_Admission, bool]:
        try:
            operation_id = str(UUID(operation_id))
        except ValueError as exc:
            raise HubFailure(422, "OPERATION_ID_INVALID", "Idempotency-Key must be a UUID.") from exc
        signature = method, path, hashlib.sha256(body).hexdigest()
        try:
            payload = json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        with self._lock:
            existing = self._operations.get(operation_id)
            if existing:
                if existing.signature != signature:
                    raise HubFailure(409, "OPERATION_ID_CONFLICT", "This operation id already names a different request.")
                return existing, False
            route = urlsplit(path).path
            published = (retained or {}).get("published", {})
            candidate = f"hub-cand-{UUID(operation_id).hex}" if method == "POST" and _CANDIDATE_REQUEST.fullmatch(route) else None
            accepted = _ACCEPT_REQUEST.fullmatch(route) if method == "POST" else None
            if accepted:
                candidate = accepted.group(1)
            proposal = _PROPOSAL_CANDIDATE.fullmatch(route)
            record = OperationRecord(
                operationId=operation_id, projectId=self.project_id, kind=f"{method} {route}",
                source=source, status="committing" if accepted else "executing",
                baseRevision=published.get("version"),
                baseDigest=payload.get("stateDigest") or published.get("stateSha256"),
                sourceRunId=payload.get("sourceRunId"),
                sourceStageRef=payload.get("sourceStageRef") or payload.get("expectedHeadStageRef"),
                proposalId=proposal.group(1) if proposal else None,
                candidateId=candidate, sessionId=session_id,
            )
            expected_stage, branch_id = payload.get("expectedHeadStageRef"), payload.get("branchId", "main")
            admission = _Admission(record, signature,
                expected_stage if isinstance(expected_stage, str) else None,
                branch_id if isinstance(branch_id, str) else "main", bool(accepted))
            self._operations[operation_id] = admission
            try:
                self._save()  # Must succeed before a caller can dispatch this request.
            except HubFailure:
                del self._operations[operation_id]
                raise
            return admission, True

    def replied(self, admission: _Admission, response: HttpResult) -> None:
        with self._lock:
            admission.response = response
            payload = response.json()
            record = admission.record
            if payload.get("baseStateDigest"):
                record.baseDigest = payload["baseStateDigest"]
                record.baseRecordDigest = payload.get("recordDigest")
                record.sourceRunId = payload.get("sourceRunId")
                record.sourceStageRef = payload.get("sourceStageRef")
            for name in ("proposalId", "jobId", "candidateId"):
                if isinstance(payload.get(name), str):
                    setattr(record, name, payload[name])
            if response.status >= 400:
                record.status = "stale" if "STALE" in str(payload.get("code", "")) else "failed"
                record.reason = str(payload.get("detail", f"HTTP {response.status}"))[:1200]
            elif record.candidateId:
                # Even a 200 accept or successful job needs retained evidence.
                record.status = "committing" if admission.accepting_candidate else "executing"
            else:
                record.status = "completed"
            try:
                self._save()
            finally:
                admission.finished.set()

    def interrupted(self, admission: _Admission, reason: str) -> None:
        with self._lock:
            admission.record.status = "needs_recovery"
            admission.record.reason = reason
            try:
                self._save()
            finally:
                admission.finished.set()

    def bind_proposal(self, admission: _Admission, proposal: dict, base_revision: int):
        with self._lock:
            record = admission.record
            record.baseRevision = base_revision
            record.baseDigest = proposal["baseStateDigest"]
            record.baseRecordDigest = proposal["recordDigest"]
            record.sourceRunId = proposal.get("sourceRunId")
            record.sourceStageRef = proposal.get("sourceStageRef")
            record.status = "validated"
            self._save()

    def reconcile(self, retained: dict, *, worker_alive: bool) -> None:
        candidates = {row["candidateId"]: row for row in retained.get("candidates", [])}
        jobs = {row["candidateId"]: row for row in retained.get("jobs", [])}
        stages = retained.get("stages", [])
        # A stage must be reachable from a committed branch. Prepared files are
        # intentionally absent from the existing inspector's answer.
        if not stages:
            stages = [stage for branch in retained.get("branches", []) for stage in branch.get("stages", [])]
        with self._lock:
            for admission in self._operations.values():
                record = admission.record
                if record.status in {"failed", "stale", "cancelled"}:
                    continue
                candidate = candidates.get(record.candidateId)
                job = jobs.get(record.candidateId)
                if job:
                    record.jobId = job.get("jobId")
                    record.proposalId = record.proposalId or job.get("proposalId")
                if admission.accepting_candidate and isinstance(admission.expected_stage, str) and admission.expected_stage:
                    committed = next((stage for stage in stages
                        if stage.get("candidateId") == record.candidateId
                        and stage.get("branchId") == admission.branch_id
                        and stage.get("parentStageRef") == admission.expected_stage), None)
                    if committed:
                        record.status, record.committed, record.reason = "completed", True, None
                        record.resultDigest = committed.get("recordDigest") or committed.get("stateDigest")
                        # Stage acceptance is independent of formal issue.
                        record.resultRevision = None
                        continue
                elif not admission.accepting_candidate and candidate and candidate.get("status") in {"completed", "succeeded"}:
                    record.status, record.reason = "completed", None
                    record.resultDigest = candidate.get("resultStateDigest") or candidate.get("resultRecordDigest")
                    record.baseDigest = candidate.get("baseStateDigest") or record.baseDigest
                    record.baseRecordDigest = candidate.get("baseRecordDigest") or record.baseRecordDigest
                    record.baseRevision = (candidate.get("base") or {}).get("version", record.baseRevision)
                    continue
                if candidate and candidate.get("status") == "failed":
                    record.status = "failed"
                    record.reason = candidate.get("error") or "The retained candidate reports incomplete execution."
                    continue
                if job and worker_alive:
                    status = job.get("status")
                    record.status = {"queued": "queued", "running": "executing", "failed": "failed"}.get(status, "needs_recovery")
                    record.reason = job.get("error")
                elif record.candidateId and not worker_alive and record.status in _ACTIVE:
                    record.status = "needs_recovery"
                    record.reason = "The worker stopped before a complete retained result could be verified. This operation was not replayed."
            observed = {}
            tracked = {row.record.candidateId for row in self._operations.values()}
            for candidate_id, candidate in candidates.items():
                if candidate_id in tracked:
                    continue
                status = candidate.get("status", "needs_recovery")
                observed[candidate_id] = OperationRecord(
                    operationId=f"candidate:{candidate_id}", projectId=self.project_id,
                    kind="candidate", source="retained", candidateId=candidate_id,
                    status={"succeeded": "completed", "running": "executing"}.get(status, status),
                    baseRevision=(candidate.get("base") or {}).get("version"),
                    baseRecordDigest=candidate.get("baseRecordDigest"),
                    baseDigest=candidate.get("baseStateDigest"), resultDigest=candidate.get("resultStateDigest"),
                    jobId=candidate.get("jobId"), proposalId=candidate.get("proposalId"),
                    committed=bool(candidate.get("commitStageRefs")),
                    reason=candidate.get("error"),
                )
            self._retained = observed

    def records(self) -> list[OperationRecord]:
        with self._lock:
            values = [row.record for row in self._operations.values()]
            # Keep active work visible even after many completed requests.
            active = [row for row in values if row.status in _ACTIVE or row.status == "needs_recovery"]
            recent = [row for row in values if row not in active][-50:]
            return [row.model_copy(deep=True) for row in [*active, *recent, *self._retained.values()]]

    def candidate_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(dict.fromkeys(row.record.candidateId for row in self._operations.values()
                if row.record.candidateId and row.record.status in _ACTIVE | {"needs_recovery"}))


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
    sample: tuple[int, int] | None = None
    hashed_at_ns: int | None = None
    observed_sha256: str | None = None
    failure: HubError | None = None


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
    wake: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    last_workers: tuple = ()
    last_snapshot: dict | None = None
    projection_key: tuple | None = None
    # Keyed by the copy's origin page identity, which never moves. Runtime
    # lifetime only: the copies themselves are the project's, and which ones
    # exist is derived from it, never remembered here.
    work_copies: dict[tuple[str, str, str | None, int], _WorkCopyObservation] = field(default_factory=dict)
    # A project whose documents will not list at all. Separate from ``error``
    # because it is not a fact about the retained projection.
    work_copy_error: HubError | None = None


class ProjectRuntimeManager:
    def __init__(self, applications, chats):
        self.applications, self.chats = applications, chats
        self.server_id = str(uuid4())
        self.events = StudioEvents(buffer_size=256)
        self._lock = threading.RLock()
        self._projects: dict[str, ProjectRuntime] = {}
        self._closing = threading.Event()
        self._clients = 0
        self._chat_changed = set()

    def emit(self, kind: str, runtime_id: str | None = None):
        self.events.publish(event={"kind": kind, "runtimeId": runtime_id})

    def chat_changed(self, session):
        # Called under ChatStore's lock: no lock inversion, IO or project open.
        with self._lock:
            self._chat_changed.add(project_key(session.projectDir))

    def open(self, project_id: str, project_dir: str) -> ProjectRuntime:
        actual_id, actual_dir = _project(project_dir)
        if actual_id != project_id:
            raise HubFailure(409, "PROJECT_MISMATCH", "The requested project identity does not match this folder.")
        key = project_key(actual_dir)
        runtime_id = str(uuid5(NAMESPACE_URL, f"{actual_id}:{key}"))
        with self._lock:
            if self._closing.is_set():
                raise HubFailure(409, "HUB_STOPPING", "Hub is closing.")
            runtime = self._projects.get(runtime_id)
            if runtime is None:
                settings = StudioSettings(project_dir=Path(actual_dir), cad_export="off")
                binding = ProjectBinding(FilesystemProjectRepository.open(Path(actual_dir)),
                    project_id=actual_id, project_dir=Path(actual_dir), settings=settings)
                operations = OperationManager(actual_id, project_dir=actual_dir,
                    journal_path=self.applications.runtime_root / "runtime/operations" / f"{runtime_id}.json")
                runtime = ProjectRuntime(runtime_id, actual_id, actual_dir, operations, binding)
                self._projects[runtime_id] = runtime
            if runtime.state == "closed":
                runtime.state = "open"
            if runtime.thread is None or not runtime.thread.is_alive():
                runtime.thread = threading.Thread(target=self._watch, args=(runtime,), daemon=True, name=f"hub-project-{project_id}")
                runtime.thread.start()
                self.emit("project/opened", runtime_id)
            runtime.wake.set()
            return runtime

    def get(self, runtime_id: str, project_id: str | None = None) -> ProjectRuntime:
        with self._lock:
            runtime = self._projects.get(runtime_id)
        if runtime is None:
            raise HubFailure(404, "RUNTIME_NOT_FOUND", "Open the project's runtime first.")
        if project_id is not None and project_id != runtime.project_id:
            raise HubFailure(409, "PROJECT_MISMATCH", "This request names another project.")
        if _project(runtime.project_dir) != (runtime.project_id, runtime.project_dir):
            raise HubFailure(409, "PROJECT_MISMATCH", "The runtime's project binding changed.")
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

    def project_snapshot(self, runtime: ProjectRuntime) -> ProjectRuntimeDto:
        sessions = [row for row in [*self.chats.list(), *self.chats.list(archived=True)]
                    if row.projectId == runtime.project_id and project_key(row.projectDir) == project_key(runtime.project_dir)]
        workers = [worker_dto(row) for row in self.applications.worker_snapshots(project_dir=runtime.project_dir)]
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
        return HubRuntimeDto(serverId=self.server_id, sequence=buffered[-1]["seq"] if buffered else 0,
            projects=[self.project_snapshot(runtime) for runtime in runtimes],
            workers=[worker_dto(row) for row in self.applications.worker_snapshots()])

    def _read_retained(self, runtime: ProjectRuntime, *, worker=None) -> dict:
        from archflow_studio_api.application.runtime import inspect_runtime
        from archflow_studio_api.transport.runtime import runtime_dto
        ids = runtime.operations.candidate_ids()
        chunks = [ids[offset:offset + 200] for offset in range(0, len(ids), 200)] or [()]
        result = None
        candidates = {}
        for chunk in chunks:
            if worker is None:
                current = runtime_dto(inspect_runtime(runtime.binding, candidate_ids=chunk)).model_dump(by_alias=True)
            else:
                query = urlencode([("candidateId", value) for value in chunk])
                response = request_http(worker.url, "/api/runtime" + (f"?{query}" if query else ""), timeout=10)
                if response.status != 200:
                    raise HubFailure(503, "RUNTIME_READ_FAILED", "The bound Studio could not read its runtime state.")
                current = response.json()
            if result is not None and (result.get("published"), result.get("branches")) != (current.get("published"), current.get("branches")):
                raise HubFailure(409, "RUNTIME_CHANGED", "The project changed during runtime inspection; read its next snapshot.")
            candidates.update((row["candidateId"], row) for row in current.get("candidates", []))
            result = current
        result["candidates"] = list(candidates.values())
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
        if alive and projection_key != runtime.projection_key:
            # Rebuild through the existing state projection owner after a
            # worker/retained-base change. This is a read, never candidate replay.
            projection = request_http(worker.url, "/api/state", timeout=10)
            if projection.status != 200:
                raise HubFailure(503, "PROJECTION_UNAVAILABLE", "The recovered Studio could not rebuild its retained state projection.")
        with runtime.lock:
            previous = runtime.retained
            runtime.retained = retained
            runtime.error = None
            runtime.operations.reconcile(retained, worker_alive=alive)
            runtime.projection = "ready" if alive else "stale" if worker else "unknown"
            runtime.projection_key = projection_key
        busy = any(row.status in _ACTIVE for row in runtime.operations.records()) or any(row.get("status") in {"queued", "running"} for row in retained.get("jobs", []))
        self.applications.set_busy(project_dir=runtime.project_dir, busy=busy)
        if previous is not None and previous.get("published") != retained.get("published"):
            self.emit("state/committed", runtime.runtime_id)
        if previous is not None and previous.get("branches") != retained.get("branches"):
            self.emit("operation/committed", runtime.runtime_id)

    @staticmethod
    def _work_copy_key(copy: DocumentWorkCopy) -> tuple[str, str, str | None, int]:
        return copy.run_id, copy.asset_sha256, copy.revision_ref, copy.page_index

    def bind_work_copies(self, runtime: ProjectRuntime) -> dict[tuple[str, str, str | None, int], str]:
        """Re-derive which of this project's registered pages have an editable file.

        The project is the only record of that: a registered single-page image
        names exactly one possible copy path and a row exists only when that
        file is there. So a copy opened before this process started is bound on
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
                observed.copy = copy
        return {key: str(copy.path) for key, copy in copies.items()}

    def _stable_work_copy_bytes(self, observed: _WorkCopyObservation) -> bytes | None:
        """The copy's settled contents, or ``None`` while it is still moving.

        A producer's save is not atomic on every path. The same
        ``(size, mtime_ns)`` has to be seen twice before the file is read, the
        sample is taken again after reading, and a digest is only trusted once
        the last write is older than the timestamp granularity that could hide
        a rewrite inside it. Half-written bytes therefore reach neither the
        project nor the user as an error.
        """

        path = observed.copy.path
        try:
            before = path.stat()
        except FileNotFoundError:
            # A transient missing file is what an atomic save/rename looks
            # like from here, not a deletion. Binding decides what exists.
            return None
        sample = (before.st_size, before.st_mtime_ns)
        if (observed.hashed_at_ns is not None and observed.sample == sample
                and before.st_mtime_ns + _WORK_COPY_SETTLED_NS <= observed.hashed_at_ns):
            return None
        if observed.sample != sample:
            observed.sample, observed.hashed_at_ns = sample, None
            return None
        try:
            data = path.read_bytes()
            after = path.stat()
        except FileNotFoundError:
            return None
        if (after.st_size, after.st_mtime_ns) != sample:
            observed.sample, observed.hashed_at_ns = (after.st_size, after.st_mtime_ns), None
            return None
        observed.hashed_at_ns = time.time_ns()
        return data

    def _observe_work_copies(self, runtime: ProjectRuntime) -> int:
        """Register what each settled work copy changed to, page for page, or say why not.

        Three cases are kept apart on purpose. Bytes this process has not seen
        move are nothing, even when the page they answer for was replaced by
        some other client — an untouched copy must never roll the Board back.
        Bytes that moved to what the project already says are equally nothing.
        Anything else moved, including an undo to an earlier registration, and
        is offered to the document owner: it either becomes the next registered
        revision or its refusal is carried to the user, per copy, unchanged.
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
                if observed.failure and observed.failure.code == "WORK_COPY_READ_FAILED":
                    observed.failure = None
                continue
            # The head can have moved since the last binding pass; aim this
            # replacement at the page the project answers for right now.
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
            observed.observed_sha256 = digest
            if digest == copy.head_asset_sha256:
                # The copy agrees with the page again. Whatever was refused
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
                    # The copy is one file standing for the whole document, so
                    # it answers for every page of it: page i replaces page i.
                    "replacesPages": [{"runId": copy.head_run_id,
                        "assetSha256": copy.head_asset_sha256, "revisionRef": copy.head_revision_ref,
                        "pageIndex": copy.head_page_index + page, "newPageIndex": page}
                        for page in range(copy.page_count)],
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

    def _watch(self, runtime: ProjectRuntime):
        next_retained_read = 0.0
        while not self._closing.is_set():
            force_read = runtime.wake.is_set()
            runtime.wake.clear()
            workers = self.applications.worker_snapshots(project_dir=runtime.project_dir)
            worker_states = tuple((row.instance_id, row.state, row.healthy) for row in workers)
            drained = runtime.state == "closed" and not any(row.process_id is not None for row in workers)
            active = any(row.status in _ACTIVE for row in runtime.operations.records()) or any(
                row.get("status") in {"queued", "running"} for row in (runtime.retained or {}).get("jobs", []))
            due = (drained or force_read or active or worker_states != runtime.last_workers
                   or time.monotonic() >= next_retained_read)
            try:
                if due:
                    # Keep liveness/session reads responsive without rebuilding
                    # unchanged retained history on every idle heartbeat. Hub
                    # mutations wake this observer; the fallback sees changes
                    # made through a separate Studio/project client.
                    self.refresh(runtime, cold=drained)
                    next_retained_read = time.monotonic() + _IDLE_RETAINED_REFRESH_S
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
                if due:
                    # Which copies exist is re-derived on the retained cadence;
                    # their bytes are watched every heartbeat, which costs one
                    # stat each once a copy has settled.
                    self.bind_work_copies(runtime)
                self._observe_work_copies(runtime)
            except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
                with runtime.lock:
                    runtime.work_copy_error = HubError(code="WORK_COPY_READ_FAILED", detail=str(exc)[:1200])
            else:
                with runtime.lock:
                    runtime.work_copy_error = None
            with self._lock:
                chat_changed = project_key(runtime.project_dir) in self._chat_changed
                self._chat_changed.discard(project_key(runtime.project_dir))
            snapshot = self.project_snapshot(runtime).model_dump()
            if snapshot != runtime.last_snapshot or chat_changed:
                previous = runtime.last_snapshot
                runtime.last_snapshot = snapshot
                self.emit("agent/progress" if chat_changed else "project/updated", runtime.runtime_id)
                if previous and previous.get("projection") != snapshot["projection"]:
                    self.emit("projection/updated" if snapshot["projection"] == "ready" else "projection/invalidated", runtime.runtime_id)
            if drained:
                break
            runtime.wake.wait(1)

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
                self.emit("operation/failed", runtime.runtime_id)
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
