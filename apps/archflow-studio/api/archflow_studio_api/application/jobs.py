"""Where a candidate run happens, and how it is watched while it does.

``run_project`` calls ``asyncio.run`` internally, so it cannot be called on the
event loop thread at all: it would refuse. This module owns the worker
threads it runs on instead, and the queue in front of them.

The queue is dependency-aware. Two candidates conflict when their closures
intersect - the target, the kernel's direct and propagated impact and what
the sentence protected - and a conflicting candidate waits for the one ahead
of it, saying which one and why. Candidates whose closures are disjoint run
side by side: each run writes only its own directory under the repository's
lock, and a harness run never touches HEAD. Exports are the exception: a
Rhino export is one process on this machine, so a candidate that exports
takes the exclusive lane and waits for any other exporting candidate,
whatever their closures. The DAG is never drawn; it shows as behaviour.

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
    closure: frozenset[str] = frozenset()
    waiting_for: str | None = None
    waiting_reason: str | None = None


class JobRegistry:
    """The worker threads candidates run on, the queue in front of them, and
    what became of each job.

    Every state change publishes an event before the lock is released, so the
    order events are numbered in is the order the jobs actually changed.
    """

    def __init__(self, events: StudioEventSink, *, max_workers: int = 2) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._events = events
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._by_candidate: dict[str, str] = {}
        # Admission order: a job waits behind every earlier job it conflicts
        # with, so two changes to one element run in the order they were asked.
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

    def submit(
        self,
        *,
        candidate_id: str,
        proposal_id: str,
        work: Callable[[], Any],
        closure: Iterable[str] = (),
        exclusive: bool = False,
    ) -> Job:
        """Queue one candidate run and answer with the job that will do it.

        ``closure`` is what the change touches, as the record's refs; two
        jobs whose closures intersect never run at the same time. An
        ``exclusive`` job (one that exports) never runs beside another
        exclusive job.

        A candidate id is claimed here, once. Rebinding one to a second job
        would silently orphan the first: ``GET /api/candidates/{id}`` would
        start answering for a different run, and the job that actually made
        those records would become unreachable through the id it was given.
        The second submission is refused instead, naming both jobs.
        """

        job = Job(
            job_id=f"job-{uuid4().hex[:12]}",
            status=QUEUED,
            candidate_id=candidate_id,
            proposal_id=proposal_id,
            created_at=_now(),
            lane=EXCLUSIVE if exclusive else PARALLEL,
            closure=frozenset(closure),
        )
        with self._lock:
            claimed = self._by_candidate.get(candidate_id)
            if claimed is None:
                self._jobs[job.job_id] = job
                self._by_candidate[candidate_id] = job.job_id
        if claimed is not None:
            raise StudioError(
                409,
                "CANDIDATE_ID_COLLISION",
                f"candidate {candidate_id} is already claimed by job "
                f"{claimed}. One candidate id names one run; it is never "
                "rebound to a second job.",
            )
        self._publish(job, "candidate.queued")
        with self._lock:
            self._pending.append(job.job_id)
        self._admit(work_for={job.job_id: work})
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

    def shutdown(self) -> None:
        """Stop accepting work and let the running candidates finish."""

        self._workers.shutdown(wait=True)

    # ---- admission

    def _blocker(self, job: Job, ahead: Iterable[Job]) -> tuple[str, str] | None:
        """The nearest job ahead that this one must wait for, and why.

        Nearest, not first: behind two changes to one element the third
        waits for the second, which is the one it will actually run after.
        """

        for other in reversed(list(ahead)):
            if job.lane == EXCLUSIVE and other.lane == EXCLUSIVE:
                return other.candidate_id, EXCLUSIVE_REASON
            shared = sorted(job.closure & other.closure)
            if shared:
                return other.candidate_id, "shares " + ", ".join(shared)
        return None

    def _admit(self, *, work_for: Mapping[str, Callable[[], Any]] | None = None) -> None:
        """Start every pending job that nothing ahead of it blocks.

        Called on submit and whenever a job finishes. Pending jobs are
        looked at in order; each is blocked by any running job or any
        *earlier* pending job it conflicts with, so admission never
        reorders two jobs that touch the same thing. A job that is not
        blocked but finds every worker busy waits for a worker, and says so.
        """

        started: list[tuple[Job, Callable[[], Any]]] = []
        waiting: list[tuple[Job, str]] = []
        with self._lock:
            if work_for:
                self._work.update(work_for)
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
