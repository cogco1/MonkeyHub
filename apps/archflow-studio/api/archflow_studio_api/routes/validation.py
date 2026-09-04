"""``GET /api/candidates/{id}/validation``: what the kernel says, and the verdict.

The route does three things and delegates the rest. It refuses a live job
while that job is still in flight, then reads the candidate back with the same
``describe`` the candidate readout uses — from its exact retained P036 harness
receipt when process-local job or proposal memory is absent — so a client
cannot be shown a verdict about one set of seat results and a readout about
another.

The refusal is worth its own sentence. A candidate that is queued or running
has no records to validate, and answering 200 with an empty or failing receipt
would let "not finished" read as "did not pass". It is a 409 that names the job
to poll instead.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.candidate import describe
from ..application.jobs import QUEUED, RUNNING, SUCCEEDED, Job
from ..application.validation import validate_candidate, validation_key
from ..transport.errors import StudioError
from ..transport.validation import ValidationDto
from ..transport.validation import to_dto as validation_dto

router = APIRouter(tags=["validation"])


@router.get(
    "/candidates/{candidate_id}/validation",
    response_model=ValidationDto,
    response_model_by_alias=True,
)
def read_validation(request: Request, candidate_id: str) -> ValidationDto:
    """Validate one finished candidate against the project's published design."""

    state = request.app.state
    binding = bound_project(state)
    try:
        job: Job | None = state.jobs.for_candidate(candidate_id)
    except StudioError as exc:
        if exc.code != "CANDIDATE_NOT_FOUND":
            raise
        # Completed execution is project history, not process memory.  The
        # same exact P036 harness receipt used by GET /api/candidates proves
        # whether this retained run is a Studio candidate.
        job = None
    if job is not None and job.status in (QUEUED, RUNNING):
        raise StudioError(
            409,
            "CANDIDATE_NOT_FINISHED",
            f"candidate {candidate_id} is still {job.status}; job "
            f"{job.job_id} has not finished writing the records a receipt "
            f"would be made from. Poll GET /api/jobs/{job.job_id} and ask "
            "again when it reports succeeded.",
        )
    proposal = None
    if job is not None and job.status == SUCCEEDED:
        try:
            proposal = state.proposals.get(job.proposal_id)
        except StudioError:
            # Program sheets and selected options are candidates without a
            # Proposal.  Their retained run is still independently readable.
            pass
    candidate = describe(
        binding,
        proposal,
        candidate_id=candidate_id,
        job_id=job.job_id if job is not None else None,
        status=job.status if job is not None else SUCCEEDED,
        proposal_id=job.proposal_id if job is not None else None,
    )
    # Read once, then used both to check against and to remember under, so the
    # verdict and the key it is filed under name the same canonical version.
    head = binding.head()
    return validation_dto(
        # Computed on the first request for this candidate at this issue and
        # remembered: a finished run's records do not change, so a client
        # polling the readout must not appear on the event stream as a server
        # deciding again. A new issue is a different question, and gets a
        # fresh answer and a fresh event.
        state.validations.remembered(
            validation_key(candidate_id, head),
            lambda: validate_candidate(
                head,
                candidate,
                events=state.events,
            ),
        )
    )
