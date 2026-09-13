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

Running a candidate is a preview, not a judgement. It records nothing about
the proposal beyond the link the job registry already holds (proposal id to
candidate id) and what the run itself retains; it does not mark the proposal
accepted, and it does not close the other proposals made against the same
base. Accepting is the architect's own act: ``POST /api/proposals/{id}/decision``
with ``accepted`` and this run's ``candidateId`` (``episodes.accept``). The one
thing the run does for the episode store is give the judgements the architect
already made against this base — a rejection, a modification — a run to be
written into, so they stop being process memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, Query
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError

from ..application.binding import ProjectBinding, bound_project
from ..application.candidate import CandidateRun, describe, execute_candidate, prepare_combined_candidate, run_operator
from ..application.compare import compare_runs, shapes_of
from ..application.jobs import FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobRegistry
from ..application.monitoring import projection_source_ref
from ..application.projection import (
    StateProjection,
    project_state,
    require_actionable,
)
from ..application.proposals import read_refs_of, write_refs_of, Proposal
from ..settings import StudioSettings
from ..transport.candidate import (
    CandidateAcceptedDto,
    CombineCandidatesRequestDto,
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
    projection = project_state(binding, run_id=proposal.source_run_id, source_stage_ref=proposal.source_stage_ref)
    require_actionable(projection)
    _require_current_base(binding, projection, proposal)
    registry: JobRegistry = state.jobs
    settings: StudioSettings = state.settings
    run_id = _run_id(proposal_id)

    def work() -> object:
        receipt = execute_candidate(binding, settings, proposal, run_id, monitor=state.monitor)
        # No judgement is made here: a candidate the architect asked to see is
        # not a proposal the architect accepted, and the other proposals
        # against this base stay open. Only the judgements already made
        # against this base — held in process until now — are flushed into
        # the run that exists. A refused value or a stale base raises above
        # this line, the job reports it, and nothing is written into a run
        # that never happened.
        state.episodes.flush(
            binding.repository,
            binding.load_run(run_id),
            proposal.base_state_digest,
        )
        return receipt

    return accepted_dto(
        registry.submit(
            candidate_id=run_id,
            proposal_id=proposal_id,
            # The work runs on a registry worker thread: ``run_project``
            # calls ``asyncio.run`` and would refuse to start on the loop.
            work=work,
            project_id=binding.project_id,
            source_ref=projection_source_ref(projection),
            related_event_id=(
                f"studio:model:{proposal.compilation_receipt['receipt_id']}"
                if proposal.compilation_receipt and proposal.compilation_receipt.get("receipt_id") else None
            ),
            # The queue's two facts about this run: what it touches, and
            # whether it needs the one Rhino this machine can export with. An
            # OCCT export is ordinary worker work and takes no lane of its own.
            read_refs=read_refs_of(proposal),
            write_refs=write_refs_of(proposal),
            exclusive=settings.rhino_lane,
        )
    )


@router.post("/candidates/combine", response_model=CandidateAcceptedDto, response_model_by_alias=True, status_code=202)
def combine_candidates(request: Request, body: CombineCandidatesRequestDto) -> CandidateAcceptedDto:
    state = request.app.state
    binding = bound_project(state)
    if body.project_id != binding.project_id:
        raise StudioError(409, "PROJECT_MISMATCH", "This request names another project.")
    candidate_ids = tuple(body.candidate_ids)
    projection, operator = prepare_combined_candidate(binding, candidate_ids)
    run_id = _run_id("combined")
    return accepted_dto(state.jobs.submit(
        candidate_id=run_id, proposal_id="combined:" + "+".join(candidate_ids),
        work=lambda: run_operator(binding, state.settings, operator, run_id,
                                  source_run_id=projection.run.run_id, source_stage_ref=projection.source_stage_ref,
                                  combined_candidate_ids=candidate_ids, monitor=state.monitor),
        project_id=binding.project_id, source_ref=projection_source_ref(projection),
        read_refs=frozenset(operator.protected), exclusive=state.settings.rhino_lane,
    ))


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
        return _candidate_with_objects(
            binding,
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
        return _candidate_with_objects(
            binding,
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
    return _candidate_with_objects(
        binding,
        describe(
            binding,
            proposal,
            candidate_id=candidate_id,
            job_id=job.job_id,
            status=SUCCEEDED,
            proposal_id=job.proposal_id,
        )
    )


def _candidate_with_objects(binding: ProjectBinding, candidate: CandidateRun) -> CandidateDto:
    if candidate.status != SUCCEEDED:
        return candidate_dto(candidate, object_readback_error=f"Candidate is {candidate.status}; retained object inspection is unavailable.")
    try:
        shapes = shapes_of(binding, candidate.candidate_id)
    except StudioError as exc:
        return candidate_dto(candidate, object_readback_error=f"{exc.code}: {exc.detail}")
    except ProjectRepositoryError as exc:
        return candidate_dto(candidate, object_readback_error=str(exc))
    missing = [seat.seat_id for seat in candidate.seat_results if not (seat.cad or {}).get("inspection_ref")]
    return candidate_dto(
        candidate, shapes=shapes,
        object_readback_error=f"No retained object inspection for seats: {', '.join(missing)}." if missing else None,
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
