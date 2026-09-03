"""``/api/proposals``: one sentence about one selection becomes a typed proposal.

Nothing on this route executes anything. It projects the bound project, hands
the utterance to the deterministic seam, keeps the answer in this process so a
later ``GET`` can show the same proposal, and returns it. Running a proposal as
a candidate is Task 7's, and it starts from the operator this route already
returned.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import ProjectBinding, bound_project
from ..application.intent import DeterministicIntentProvider
from ..application.projection import project_state
from ..application.proposals import proposal_from
from ..transport.errors import StudioError
from ..transport.proposal import ProposalDto, ProposalRequestDto, to_dto

router = APIRouter(tags=["proposals"])


@router.post(
    "/proposals",
    response_model=ProposalDto,
    response_model_by_alias=True,
    status_code=201,
)
def create_proposal(
    request: Request, body: ProposalRequestDto
) -> ProposalDto:
    """Propose one change against the exact state the client was given."""

    binding = bound_project(request.app.state)
    _require_bound_project(binding, body.project_id)
    projection = project_state(binding)
    proposal = proposal_from(
        DeterministicIntentProvider(projection).propose(
            # Round 1 has no session identity: the project the request is bound
            # to is what answers for it, and nothing about who asked changes
            # what the record says.
            session_ref=f"project:{binding.project_id}",
            message=body.utterance,
            context_refs=body.context_refs(),
        )
    )
    return to_dto(request.app.state.proposals.put(proposal))


@router.get(
    "/proposals/{proposal_id}",
    response_model=ProposalDto,
    response_model_by_alias=True,
)
def read_proposal(request: Request, proposal_id: str) -> ProposalDto:
    """One proposal this process still holds, exactly as it was returned."""

    return to_dto(request.app.state.proposals.get(proposal_id))


def _require_bound_project(
    binding: ProjectBinding, project_id: str | None
) -> None:
    """A client that believes it is elsewhere is told where it actually is.

    The check comes before the base check because a digest from another project
    would otherwise be reported as merely stale, which reads as "refresh and
    retry" — the one thing that cannot help here.
    """

    if project_id is None or project_id == binding.project_id:
        return
    raise StudioError(
        403,
        "PROJECT_MISMATCH",
        f"the proposal names project {project_id}, and this API is bound to "
        f"{binding.project_id}. One process answers for one project; it never "
        "switches on a request.",
    )
