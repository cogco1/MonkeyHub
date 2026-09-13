"""Where a candidate run happens, and how it is watched while it does.

``run_project`` calls ``asyncio.run`` internally, so it cannot be called on the
event loop thread at all: it would refuse. This module owns the worker
threads it runs on instead, and the queue in front of them.

Candidates run in separate workspaces against immutable sources. Their read
and write refs describe the proposed change; even overlapping edits can be
computed independently. Deciding whether their results can be combined is
not queue admission. A Rhino export still takes the exclusive lane because
there is one Rhino process on this machine; other candidates share the worker
pool without a design-ref lock.

The registry is an in-process dict, like the proposal store and for the same
reason: it is not history. It remembers what this service did since it started
so a client can ask whether its job finished; what the *project* did is in the
run records, which outlive it.

Failure is the part worth reading. Any exception from the work - the runner's
``ProjectRunnerError``, an absent seat pack, a repository refusal - lands on
the job as ``failed`` with the exception's own sentence. Nothing is swallowed,
nothing is retried, and nothing is reported as an empty success.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from ..ports import StudioEventSink
from ..transport.errors import StudioError
from .monitoring import StudioMonitor, candidate_event_id

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

PARALLEL = "parallel"
EXCLUSIVE = "exclusive"

EXCLUSIVE_REASON = "the export lane: one Rhino export at a time on this machine"

# What this registry keeps, and what it does not. It travels on the wire so a
# client showing a job has been told what kind of thing it is looking at.
PERSISTENCE = "in-memory (not version history)"


@dataclass(frozen=True, slots=True)
class Job:
    """One candidate run, as this process last saw it."""

    job_id: str
    status: str
    candidate_id: str
    proposal_id: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    wall_time_s: float | None = None
    # The queue's own facts: which lane the job runs in, and while it is
    # queued, which candidate it is waiting for and why.
    lane: str = PARALLEL
    read_refs: frozenset[str] = frozenset()
    write_refs: frozenset[str] = frozenset()
    waiting_for: str | None = None
    waiting_reason: str | None = None


class JobRegistry:
    """The worker threads candidates run on, the queue in front of them, and
    what became of each job.

    A queued job is registered with its work and announced before another
    submitter or a finishing worker can admit it.
    """

    def __init__(self, events: StudioEventSink, *, max_workers: int = 2, monitor: StudioMonitor | None = None) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._events = events
        self._monitor = monitor
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._accepting = True
        self._jobs: dict[str, Job] = {}
        self._by_candidate: dict[str, str] = {}
        # Admission order preserves the exclusive resource lane's order.
        self._pending: list[str] = []
        self._running: list[str] = []
        self._work: dict[str, Callable[[], Any]] = {}
        self._max_workers = max_workers
        self._workers = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="studio-candidate"
        )

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting

    def stop_accepting(self) -> None:
        """Close admission while already accepted work keeps its place."""

        with self._lock:
            self._accepting = False

    def submit(
        self,
        *,
        candidate_id: str,
        proposal_id: str,
        work: Callable[[], Any],
        read_refs: Iterable[str] = (),
        write_refs: Iterable[str] = (),
        exclusive: bool = False,
        project_id: str | None = None,
        source_ref: str | None = None,
        related_event_id: str | None = None,
    ) -> Job:
        """Queue one candidate run and answer with the job that will do it.

        ``read_refs`` and ``write_refs`` describe the design inputs and edits.
        Separate candidate workspaces may compute overlapping edits. An
        ``exclusive`` job never runs beside another exclusive job.

        A candidate id is claimed here, once. Rebinding one to a second job
        would silently orphan the first: ``GET /api/candidates/{id}`` would
        start answering for a different run, and the job that actually made
        those records would become unreachable through the id it was given.
        The second submission is refused instead, naming both jobs.
        """

        if self._monitor is not None:
            operation = work
            association = self._monitor.current()
            queued_at, queued_clock = _now(), time.perf_counter()
            queue_id = f"studio:queue:{uuid4()}"
            operation_id = association.get("operation_id") or (candidate_event_id(project_id, candidate_id) if project_id else queue_id)

            def measured_work():
                with self._monitor.scope(operation_id=operation_id, parent_event_id=association.get("event_id")):
                    self._monitor.record(
                        phase="candidate_queue", event_id=queue_id, status="succeeded",
                        started_at=queued_at, ended_at=_now(), duration_ms=round((time.perf_counter() - queued_clock) * 1000),
                        project_id=project_id, run_id=candidate_id, source_ref=source_ref,
                        details={"wait_reason": "worker_admission", "execution_path": "exclusive" if exclusive else "parallel"},
                    )
                    with self._monitor.measure(
                        "candidate", project_id=project_id, run_id=candidate_id,
                        source_ref=source_ref, related_event_id=related_event_id,
                        event_id=candidate_event_id(project_id, candidate_id) if project_id is not None else None,
                    ):
                        return operation()

            work = measured_work

        job = Job(
            job_id=f"job-{uuid4().hex[:12]}",
            status=QUEUED,
            candidate_id=candidate_id,
            proposal_id=proposal_id,
            created_at=_now(),
            lane=EXCLUSIVE if exclusive else PARALLEL,
            read_refs=frozenset(read_refs),
            write_refs=frozenset(write_refs),
        )
        with self._lock:
            if not self._accepting:
                raise StudioError(
                    503,
                    "STUDIO_STOPPING",
                    "Studio is shutting down and no longer accepts candidate jobs.",
                )
            claimed = self._by_candidate.get(candidate_id)
            if claimed is None:
                self._jobs[job.job_id] = job
                self._by_candidate[candidate_id] = job.job_id
                self._work[job.job_id] = work
                self._pending.append(job.job_id)
                # Admission can happen on any submitting or finishing thread.
                # Publish queued before releasing this complete job to them.
                self._publish(job, "candidate.queued")
        if claimed is not None:
            raise StudioError(
                409,
                "CANDIDATE_ID_COLLISION",
                f"candidate {candidate_id} is already claimed by job "
                f"{claimed}. One candidate id names one run; it is never "
                "rebound to a second job.",
            )
        self._admit()
        return job

    def get(self, job_id: str) -> Job:
        """One job, or a 404 that says where jobs do not survive."""

        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise StudioError(
                404,
                "JOB_NOT_FOUND",
                f"no job {job_id} in this process. Jobs are held "
                f"{PERSISTENCE}: one started before a restart, or by another "
                "process, is gone rather than hidden.",
            )
        return job

    def list(self) -> tuple[Job, ...]:
        """A coherent read of this worker's jobs, in admission order."""

        with self._lock:
            return tuple(self._jobs.values())

    def for_candidate(self, candidate_id: str) -> Job:
        """The job that ran one candidate, or a 404 naming the candidate."""

        with self._lock:
            job_id = self._by_candidate.get(candidate_id)
        if job_id is None:
            raise StudioError(
                404,
                "CANDIDATE_NOT_FOUND",
                f"no candidate {candidate_id} was run by this process. "
                f"Candidate jobs are held {PERSISTENCE}; the run records "
                "themselves stay in the project.",
            )
        return self.get(job_id)

    def candidates_of(self, proposal_id: str) -> tuple[str, ...]:
        """Every candidate this process ran from one proposal, in job order.

        The reverse of ``for_candidate``, and read-only. It exists so that a
        judgement about a proposal can name what was actually looked at before
        it was made; nothing here starts, stops or changes a job.
        """

        with self._lock:
            return tuple(
                job.candidate_id
                for job in self._jobs.values()
                if job.proposal_id == proposal_id
            )

    def shutdown(self) -> None:
        """Finish every accepted job before closing the worker pool."""

        with self._idle:
            self._accepting = False
            self._idle.wait_for(lambda: not self._pending and not self._running)
        self._workers.shutdown(wait=True)

    # ---- admission

    def _blocker(self, job: Job, ahead: Iterable[Job]) -> tuple[str, str] | None:
        """The nearest job ahead that this one must wait for, and why.

        An exclusive job waits for the nearest earlier exclusive job.
        """

        for other in reversed(list(ahead)):
            if job.lane == EXCLUSIVE and other.lane == EXCLUSIVE:
                return other.candidate_id, EXCLUSIVE_REASON
        return None

    def _admit(self) -> None:
        """Start every pending job that nothing ahead of it blocks.

        Called on submit and whenever a job finishes. Pending jobs are
        looked at in order; each is blocked by any running job or any
        *earlier* pending job using the same exclusive resource. A job that is not
        blocked but finds every worker busy waits for a worker, and says so.
        """

        started: list[tuple[Job, Callable[[], Any]]] = []
        waiting: list[tuple[Job, str]] = []
        with self._lock:
            running = [self._jobs[job_id] for job_id in self._running]
            earlier: list[Job] = []
            still_pending: list[str] = []
            for job_id in self._pending:
                job = self._jobs[job_id]
                blocker = self._blocker(job, [*running, *earlier])
                if blocker is None and len(self._running) < self._max_workers:
                    job = replace(job, waiting_for=None, waiting_reason=None)
                    self._jobs[job_id] = job
                    self._running.append(job_id)
                    running.append(job)
                    started.append((job, self._work.pop(job_id)))
                    continue
                if blocker is None:
                    waiting_for, reason = None, (
                        f"every worker is busy ({self._max_workers} of "
                        f"{self._max_workers})"
                    )
                else:
                    waiting_for, reason = blocker
                changed = job.waiting_for != waiting_for or job.waiting_reason != reason
                job = replace(job, waiting_for=waiting_for, waiting_reason=reason)
                self._jobs[job_id] = job
                earlier.append(job)
                still_pending.append(job_id)
                if changed:
                    waiting.append((job, reason))
            self._pending = still_pending
        for job, _reason in waiting:
            self._publish(job, "candidate.waiting")
        for job, work in started:
            self._workers.submit(self._run, job.job_id, work)

    # ---- the worker thread's own half

    def _run(self, job_id: str, work: Callable[[], Any]) -> None:
        started = time.perf_counter()
        self._publish(
            self._transition(job_id, status=RUNNING, started_at=_now()),
            "candidate.running",
        )
        try:
            work()
        # Every *failure* is somebody's answer, but an interpreter that is
        # shutting down is not a failed candidate: SystemExit and
        # KeyboardInterrupt pass through rather than being recorded as one.
        except Exception as exc:  # noqa: BLE001 - the reason is the point
            # The runner's own sentence goes on the job verbatim; an exception
            # with no message would otherwise arrive as an empty string nobody
            # could act on.
            self._publish(
                self._transition(
                    job_id,
                    status=FAILED,
                    finished_at=_now(),
                    error=str(exc) or f"{type(exc).__name__} (no message)",
                    wall_time_s=round(time.perf_counter() - started, 3),
                ),
                "candidate.failed",
            )
            self._release(job_id)
            return
        except BaseException:
            self._release(job_id)
            raise
        self._publish(
            self._transition(
                job_id,
                status=SUCCEEDED,
                finished_at=_now(),
                wall_time_s=round(time.perf_counter() - started, 3),
            ),
            "candidate.succeeded",
        )
        self._release(job_id)

    def _release(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._running:
                self._running.remove(job_id)
            self._idle.notify_all()
        self._admit()

    def _transition(self, job_id: str, **changes: Any) -> Job:
        with self._lock:
            job = replace(self._jobs[job_id], **changes)
            self._jobs[job_id] = job
        return job

    def _publish(self, job: Job, event_type: str) -> None:
        self._events.publish(event=_event(job, event_type))


def _event(job: Job, event_type: str) -> Mapping[str, Any]:
    """One lifecycle event: which job, which candidate, and what it cost."""

    return {
        "type": event_type,
        "job_id": job.job_id,
        "candidate_id": job.candidate_id,
        "proposal_id": job.proposal_id,
        # The candidate id is the run id; both names travel so a client
        # watching events and a client reading run records agree.
        "run_id": job.candidate_id,
        "wall_time_s": job.wall_time_s,
        "error": job.error,
        "lane": job.lane,
        "waiting_for": job.waiting_for,
        "waiting_reason": job.waiting_reason,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
