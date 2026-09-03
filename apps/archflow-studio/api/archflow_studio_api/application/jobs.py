"""Where a candidate run happens, and how it is watched while it does.

``run_project`` calls ``asyncio.run`` internally, so it cannot be called on the
event loop thread at all: it would refuse. This module owns the one worker
thread it runs on instead. One worker, not a pool — a candidate is a real
geometry run against a real repository, and two of them writing runs into the
same project at once is a race nobody asked for. Candidates queue.

The registry is an in-process dict, like the proposal store and for the same
reason: it is not history. It remembers what this service did since it started
so a client can ask whether its job finished; what the *project* did is in the
run records, which outlive it.

Failure is the part worth reading. Any exception from the work — the runner's
``ProjectRunnerError``, an absent seat pack, a repository refusal — lands on
the job as ``failed`` with the exception's own sentence. Nothing is swallowed,
nothing is retried, and nothing is reported as an empty success.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from ..ports import StudioEventSink
from ..transport.errors import StudioError

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

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


class JobRegistry:
    """The one worker thread candidates run on, and what became of each.

    Every state change publishes an event before the lock is released, so the
    order events are numbered in is the order the jobs actually changed.
    """

    def __init__(self, events: StudioEventSink) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._by_candidate: dict[str, str] = {}
        self._workers = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="studio-candidate"
        )

    def submit(
        self,
        *,
        candidate_id: str,
        proposal_id: str,
        work: Callable[[], Any],
    ) -> Job:
        """Queue one candidate run and answer with the job that will do it.

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
        self._workers.submit(self._run, job.job_id, work)
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
        """Stop accepting work and let the running candidate finish."""

        self._workers.shutdown(wait=True)

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
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
