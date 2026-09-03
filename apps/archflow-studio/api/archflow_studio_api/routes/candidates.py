"""Running a proposal as a detached candidate, and asking how it went.

``POST /api/proposals/{id}/candidate`` answers 202 and a job id. It does not
wait: a candidate is a real geometry run, and a request that blocked on one
would time out on any project bigger than a fixture. What it *does* do before
answering is refuse for the reasons that are knowable now — a proposal that
conflicts with something the user asked to keep, and a proposal made against a
base the project has since moved off — because those are the two answers that
would otherwise arrive minutes later as a mystery.

Everything after that is the job's. A run the runner refuses is a failed job
carrying the runner's own sentence, never an HTTP error: the request to start
it succeeded, and what failed was the design.
"""

from __future__ import annotations

from datetime import datetime, timezone
import secrets

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import ProjectBinding, bound_project
from ..application.candidate import describe, execute_candidate
from ..application.jobs import Job, JobRegistry
from ..application.projection import project_state
from ..application.proposals import closure_of, Proposal
from ..settings import StudioSettings
from ..transport.candidate import (
    CandidateAcceptedDto,
    CandidateDto,
    JobDto,
    accepted_dto,
    job_dto,
)
from ..transport.candidate import to_dto as candidate_dto
from ..transport.errors import StudioError

router = APIRouter(tags=["candidates"])

# The proposal status that means "this reaches something the user asked to
# keep". It is still a proposal; it is simply not a thing to run.
CONFLICT = "conflict"


@router.post(
    "/proposals/{proposal_id}/candidate",
    response_model=CandidateAcceptedDto,
    response_model_by_alias=True,
    status_code=202,
)
def start_candidate(
    request: Request, proposal_id: str
) -> CandidateAcceptedDto:
    """Queue one proposal as a candidate run and name the run it will make."""

    state = request.app.state
    proposal = state.proposals.get(proposal_id)
    if proposal.status == CONFLICT:
        raise StudioError(
            409,
            "PROPOSAL_NOT_RUNNABLE",
            f"proposal {proposal_id} conflicts with what the utterance asked "
            f"to keep ({', '.join(proposal.impact.conflicts)}). Resolve the "
            "conflict and propose again; the studio never runs a change past "
            "a protection the user named.",
        )
    binding = bound_project(state)
    _require_current_base(binding, proposal)
    registry: JobRegistry = state.jobs
    settings: StudioSettings = state.settings
    run_id = _run_id(proposal_id)
    return accepted_dto(
        registry.submit(
            candidate_id=run_id,
            proposal_id=proposal_id,
            # The work runs on a registry worker thread: ``run_project``
            # calls ``asyncio.run`` and would refuse to start on the loop.
            work=lambda: execute_candidate(
                binding, settings, proposal, run_id
            ),
            # The queue's two facts about this run: what it touches, and
            # whether it needs the one Rhino this machine can export with.
            closure=closure_of(proposal),
            exclusive=settings.rhino_export,
        )
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobDto,
    response_model_by_alias=True,
)
def read_job(request: Request, job_id: str) -> JobDto:
    """One candidate job as this process last saw it, failures included."""

    job: Job = request.app.state.jobs.get(job_id)
    return job_dto(job)


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateDto,
    response_model_by_alias=True,
)
def read_candidate(request: Request, candidate_id: str) -> CandidateDto:
    """One finished candidate, read back out of the records its run retained."""

    state = request.app.state
    job: Job = state.jobs.for_candidate(candidate_id)
    return candidate_dto(
        describe(
            bound_project(state),
            # The proposal that was executed, for the honesty lines: what the
            # change reached is a fact about the change, and the run records
            # cannot answer it.
            state.proposals.get(job.proposal_id),
            candidate_id=candidate_id,
            job_id=job.job_id,
            status=job.status,
        )
    )


def _require_current_base(
    binding: ProjectBinding, proposal: Proposal
) -> None:
    """Refuse a proposal whose base the project has since moved off.

    The check is made here rather than at proposal time because the interval
    that matters is this one: between being shown a number and running it,
    somebody may have authored a different record. Executing against a base
    the user never saw is the one failure that would look like a success.
    """

    live = project_state(binding).state_digest
    if live == proposal.base_state_digest:
        return
    raise StudioError(
        409,
        "STALE_BASE",
        f"proposal {proposal.proposal_id} was made against state "
        f"{proposal.base_state_digest}, and {binding.project_id} now projects "
        f"{live}. Re-read /api/state and propose again: a candidate is only "
        "meaningful against the state it was proposed for.",
    )


def _run_id(proposal_id: str) -> str:
    """The candidate's run id: when it was made, what from, and which one.

    The timestamp and the proposal are what a person reads; the four random
    hex characters are what keeps two candidates of the same proposal, made
    inside one second, from naming the same run. Without them the second run
    would collide with the first — and a run id that two candidates could
    claim is a candidate id that answers for the wrong records.
    """

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"studio-cand-{stamp}-{proposal_id[-8:]}-{secrets.token_hex(2)}"
