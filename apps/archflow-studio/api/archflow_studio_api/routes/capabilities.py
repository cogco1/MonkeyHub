"""``/api/capabilities``: find the entry point that already exists, and use it.

Three reads and one write, all of them thin. The index and the description are
served from the registry beside this installation and the projection this
project is bound to; the run assembles the request the description already
described and calls the two routes that have always performed it —
``POST /api/proposals`` and ``POST /api/proposals/{id}/candidate``. There is no
capability runtime here: no second proposal store, no second queue, no second
receipt, and no acceptance or issue. A capability that could not be performed
through an existing route would not be in the index.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.capability import (
    capability,
    capability_index,
    describe_capability,
    find_with_evidence,
    require_runnable,
)
from ..application.intent import merge_keep
from ..application.projection import project_state
from ..transport.capability import (
    CapabilityDetailDto,
    CapabilityIndexDto,
    CapabilityRunDto,
    CapabilityRunRequestDto,
    detail_dto,
    index_dto,
)
from .candidates import start_candidate
from .proposals import create_proposal

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilityIndexDto, response_model_by_alias=True)
def read_capabilities(
    request: Request,
    goal: str | None = Query(
        default=None,
        description="what the user is trying to do, in English or Chinese; "
        "omitted returns the whole index",
    ),
) -> CapabilityIndexDto:
    """Which written-down capability serves this goal.

    An empty result is answered with what the index is rather than with
    silence: a capability nobody has written down may still exist as an API
    entry point, and this route is not entitled to rule that out.
    """

    matched, evidence, note = find_with_evidence(goal)
    return index_dto(matched, evidence, registered=len(capability_index()), note=note)


@router.get(
    "/capabilities/{capability_id}",
    response_model=CapabilityDetailDto,
    response_model_by_alias=True,
)
def read_capability(
    request: Request,
    capability_id: str,
    target: str | None = Query(
        default=None,
        description="the Component@1 this is about; with it the answer names "
        "the numbers that can move and what may be kept",
    ),
    element_id: str | None = Query(default=None, alias="elementId", min_length=1),
    run: str | None = Query(
        default=None,
        min_length=1,
        description="describe against this retained run instead of the "
        "project's default projection",
    ),
    source_stage_ref: str | None = Query(default=None, alias="sourceStageRef"),
) -> CapabilityDetailDto:
    """The registered entry, and what it means for the project bound now."""

    entry = capability(capability_id)
    binding = bound_project(request.app.state)
    projection = project_state(binding, run_id=run, require_view=False, source_stage_ref=source_stage_ref)
    return detail_dto(
        describe_capability(binding, projection, entry, component_id=target, element_id=element_id)
    )


@router.post(
    "/capabilities/{capability_id}/run",
    response_model=CapabilityRunDto,
    response_model_by_alias=True,
    status_code=202,
)
def run_capability(
    request: Request, capability_id: str, body: CapabilityRunRequestDto
) -> CapabilityRunDto:
    """Perform one capability: propose it, then queue the candidate.

    Both halves are the existing routes, called as they are: the proposal is
    parsed, targeted and impact-checked by ``studio.intent``, and the run is
    arranged by ``studio.candidate`` against the same exact base. A conflict
    with a kept ref raises out of the second call, so a refused change leaves
    a proposal and no run — never a candidate nobody asked for.

    Only the capability this route actually implements is performed. Writing a
    new entry into the registry describes something; it does not silently hand
    that description this route's numeric edit.
    """

    performed = require_runnable(capability(capability_id))
    proposal = create_proposal(request, body.proposal_request(merge_keep(body.utterance, body.keep)))
    accepted = start_candidate(request, proposal.proposal_id)
    return CapabilityRunDto(
        capability_id=performed,
        proposal_id=proposal.proposal_id,
        job_id=accepted.job_id,
        candidate_id=accepted.candidate_id,
        target=proposal.target.model_dump(by_alias=True),
        change=proposal.change.model_dump(by_alias=True),
        kept=list(proposal.protected),
        next=[
            f"GET /api/jobs/{accepted.job_id}",
            f"GET /api/candidates/{accepted.candidate_id}",
            f"GET /api/candidates/{accepted.candidate_id}/compare?against={proposal.source_run_id}"
            if proposal.source_run_id
            else "GET /api/candidates/{id}/compare?against=<the run this was made from>",
        ],
    )
