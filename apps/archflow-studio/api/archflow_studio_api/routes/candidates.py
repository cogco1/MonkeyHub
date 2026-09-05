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

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application import episodes
from ..application.binding import ProjectBinding, bound_project
from ..application.candidate import describe, execute_candidate
from ..application.compare import compare_runs
from ..application.jobs import FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobRegistry
from ..application.projection import (
    StateProjection,
    project_state,
    require_actionable,
)
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
from ..transport.compare import CompareDto, compare_dto
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
    projection = project_state(binding, run_id=proposal.source_run_id)
    require_actionable(projection)
    _require_current_base(binding, projection, proposal)
    registry: JobRegistry = state.jobs
    settings: StudioSettings = state.settings
    run_id = _run_id(proposal_id)
    # The judgement is made here, when the architect asks for this proposal and
    # no other — not when the run finishes. What was on the table is what this
    # process was holding at that moment, so it is read now; the episode itself
    # is only written once there is a run to write it into.
    superseded = episodes.still_open(
        state.episodes,
        state.proposals.for_state(proposal.base_state_digest),
        without=proposal_id,
    )
    read = episodes.validation_refs_read(
        state.jobs, state.validations, (proposal, *superseded)
    )
    evidence = tuple(projection.record.evidence_refs)

    def work() -> object:
        receipt = execute_candidate(binding, settings, proposal, run_id)
        # Only a run that happened carries a judgement. A refused value or a
        # stale base raises above this line, the job reports it, and nothing
        # claims a decision was retained when no run exists to hold it.
        episodes.accept(
            state.episodes,
            binding.repository,
            binding.load_run(run_id),
            project_id=binding.project_id,
            proposal=proposal,
            superseded=superseded,
            evidence_refs=evidence,
            validation_refs=read,
        )
        return receipt

    return accepted_dto(
        registry.submit(
            candidate_id=run_id,
            proposal_id=proposal_id,
            # The work runs on a registry worker thread: ``run_project``
            # calls ``asyncio.run`` and would refuse to start on the loop.
            work=work,
            # The queue's two facts about this run: what it touches, and
            # whether it needs the one Rhino this machine can export with. An
            # OCCT export is ordinary worker work and takes no lane of its own.
            closure=closure_of(proposal),
            exclusive=settings.rhino_lane,
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
    binding = bound_project(state)
    try:
        job: Job = state.jobs.for_candidate(candidate_id)
    except StudioError as exc:
        if exc.code != "CANDIDATE_NOT_FOUND":
            raise
        # Jobs and proposals are process-local execution state. A completed
        # candidate remains readable after restart only when its exact P036
        # runner receipt proves that this run used the Studio harness.
        return candidate_dto(
            describe(
                binding,
                None,
                candidate_id=candidate_id,
                job_id=None,
                status=SUCCEEDED,
                proposal_id=None,
            )
        )
    if job.status in (QUEUED, RUNNING, FAILED):
        # Only unfinished/failed execution state is answered by the in-memory
        # registry. A succeeded candidate must still prove itself below with
        # its exact retained runner receipt.
        return candidate_dto(
            describe(
                binding,
                None,
                candidate_id=candidate_id,
                job_id=job.job_id,
                status=job.status,
                proposal_id=job.proposal_id,
            )
        )
    try:
        # The proposal that was executed, for the honesty lines: what the
        # change reached is a fact about the change, and the run records
        # cannot answer it.
        proposal = state.proposals.get(job.proposal_id)
    except StudioError:
        # Not a sentence: a selected massing option, whose job names the
        # option rather than a proposal. The run's own facts are unchanged —
        # they are the records' — and the readout says which it is.
        proposal = None
    return candidate_dto(
        describe(
            binding,
            proposal,
            candidate_id=candidate_id,
            job_id=job.job_id,
            status=SUCCEEDED,
            proposal_id=job.proposal_id,
        )
    )


@router.get(
    "/candidates/{candidate_id}/compare",
    response_model=CompareDto,
    response_model_by_alias=True,
)
def compare_candidate(
    request: Request,
    candidate_id: str,
    against: str = Query(
        min_length=1,
        description="the run whose exports are the 'before': the reference "
        "run, or another candidate",
    ),
) -> CompareDto:
    """Before / After / Why: the candidate's exported objects against another
    run's, from the inspection records both runs retained.

    The counts are the records' own. The 'why' is the sentence the candidate
    was made from, when this process still holds its proposal; after a
    restart the run records still compare, and the card says the sentence
    is unavailable rather than guessing one.
    """

    state = request.app.state
    binding = bound_project(state)
    why: str | None = None
    why_source = "unavailable"
    try:
        job: Job = state.jobs.for_candidate(candidate_id)
        why = state.proposals.get(job.proposal_id).utterance
        why_source = "proposal"
    except StudioError:
        # Not this process's candidate, or its proposal is gone: the
        # comparison still stands on the records; only the sentence is missing.
        pass
    return compare_dto(
        compare_runs(
            binding,
            candidate_id=candidate_id,
            against=against,
            why=why,
            why_source=why_source,
        )
    )


def _require_current_base(
    binding: ProjectBinding,
    projection: StateProjection,
    proposal: Proposal,
) -> None:
    """Refuse a proposal whose base the project has since moved off.

    The check is made here rather than at proposal time because the interval
    that matters is this one: between being shown a number and running it,
    somebody may have authored a different record. Executing against a base
    the user never saw is the one failure that would look like a success.

    The projection is the caller's, read once: the digest this refuses on and
    the evidence the judgement cites have to come off the same reading of the
    record, or the refusal is about a state the episode does not describe.
    """

    live_state = projection.state_digest
    live_record = projection.record_digest
    if (
        live_state == proposal.base_state_digest
        and live_record == proposal.record_digest
    ):
        return
    raise StudioError(
        409,
        "STALE_BASE",
        f"proposal {proposal.proposal_id} was made against state "
        f"record {proposal.record_digest} / state "
        f"{proposal.base_state_digest}, and {binding.project_id} now has "
        f"record {live_record} / state {live_state}. Re-read /api/state and "
        "propose again: a candidate is only meaningful against the exact "
        "record it was proposed for.",
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
