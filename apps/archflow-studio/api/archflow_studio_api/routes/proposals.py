"""``/api/proposals``: one sentence about one selection becomes a typed proposal.

Nothing on this route executes anything. It projects the bound project, hands
the utterance to the deterministic seam, keeps the answer in this process so a
later ``GET`` can show the same proposal, and returns it. Running a proposal as
a candidate is Task 7's, and it starts from the operator this route already
returned.

``POST /api/proposals/{id}/decision`` is the other half of that, and it is the
only half that retains anything at all. Accepting a proposal has always left a
run; turning one down left nothing, so the option that was considered and not
chosen vanished with the chat. This route makes that judgement a
``DeliberationEpisode`` — held in this process, and written into the next
candidate run made against the same state (§5.2). A ``modified`` decision
closes the option and re-proposes the architect's replacement sentence through
the same deterministic path, so the two are one linked judgement rather than
two unrelated proposals.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.datastructures import State
from starlette.requests import Request

from ..application import episodes
from ..application.binding import ProjectBinding, bound_project
from ..application.intent import DeterministicIntentProvider
from ..application.projection import (
    StateProjection,
    project_state,
    require_actionable,
)
from ..application.proposals import Proposal, proposal_from
from ..transport.errors import StudioError
from ..transport.proposal import (
    EpisodeDto,
    ProposalDecisionRequestDto,
    ProposalDto,
    ProposalRequestDto,
    episode_dto,
    to_dto,
)

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
    require_actionable(projection)
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


@router.post(
    "/proposals/{proposal_id}/decision",
    response_model=EpisodeDto,
    response_model_by_alias=True,
    status_code=201,
)
def decide_proposal(
    request: Request, proposal_id: str, body: ProposalDecisionRequestDto
) -> EpisodeDto:
    """Turn one proposal down, or replace it, and keep the judgement."""

    state = request.app.state
    proposal = state.proposals.get(proposal_id)
    binding = bound_project(state)
    # One projection answers both questions this route asks of the record: what
    # evidence it cites, and — for a modification — what the replacement
    # sentence resolves against. Two projections could disagree.
    projection = project_state(binding)
    read = episodes.validation_refs_read(
        state.jobs, state.validations, (proposal,)
    )
    evidence = tuple(projection.record.evidence_refs)
    if body.decision == "rejected":
        if body.modified_to is not None:
            raise StudioError(
                422,
                "DECISION_INVALID",
                "a rejected decision carries no modifiedTo: rejecting closes "
                "the option, and replacing it is the modified decision.",
            )
        episode = episodes.reject(
            state.episodes,
            project_id=binding.project_id,
            proposal=proposal,
            reason=body.reason,
            evidence_refs=evidence,
            validation_refs=read,
        )
        return episode_dto(episode)
    if body.modified_to is None:
        raise StudioError(
            422,
            "DECISION_INVALID",
            "a modified decision has to say what it was modified to: send "
            "modifiedTo.utterance, the sentence that replaces the proposal.",
        )
    require_actionable(projection)
    replacement = _reproposed(
        state, binding, projection, proposal, body.modified_to.utterance
    )
    return episode_dto(
        episodes.modify(
            state.episodes,
            project_id=binding.project_id,
            proposal=proposal,
            replacement=replacement,
            reason=body.reason,
            evidence_refs=evidence,
            validation_refs=read,
        )
    )


def _reproposed(
    state: State,
    binding: ProjectBinding,
    projection: StateProjection,
    proposal: Proposal,
    utterance: str,
) -> Proposal:
    """The modified sentence, proposed again against the same selection.

    The same deterministic seam ``POST /api/proposals`` uses, given the
    selection the original proposal already resolved — the component, the
    element when there was one — so a modification cannot silently change what
    is being talked about. The base is checked by that seam: a modification
    offered against a state the project has left is ``STALE_BASE``, exactly as
    a first proposal would be.
    """

    context_refs = [
        f"state:{projection.state_digest}",
        f"component:{proposal.component_id}",
    ]
    if proposal.element_id is not None:
        context_refs.append(f"element:{proposal.element_id}")
    return state.proposals.put(
        proposal_from(
            DeterministicIntentProvider(projection).propose(
                session_ref=f"project:{binding.project_id}",
                message=utterance,
                context_refs=context_refs,
            )
        )
    )


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
