"""``/api/proposals``: one sentence about one selection becomes a typed proposal.

Nothing on this route executes anything. It projects the bound project, hands
the utterance to the deterministic seam, keeps the answer in this process so a
later ``GET`` can show the same proposal, and returns it. Running a proposal as
a candidate is Task 7's, and it starts from the operator this route already
returned.

``POST /api/proposals/{id}/decision`` is the other half of that, and it is the
only half that retains anything at all. Running a candidate has always left a
run; turning a proposal down left nothing, so the option that was considered
and not chosen vanished with the chat. This route makes that judgement a
``DeliberationEpisode`` — held in this process, and written into the next
candidate run made against the same state (§5.2). A ``modified`` decision
closes the option and re-proposes the architect's replacement sentence through
the same deterministic path, so the two are one linked judgement rather than
two unrelated proposals.

``accepted`` is the architect's explicit choice, and it names its run: the
``candidateId`` of a candidate this process ran from this very proposal and
finished. Nothing runs again, HEAD does not move and nothing is issued; the
judgement is written into that run, the other options still open against the
same base are closed as superseded by it, and the judgements held until then
against that base go into the same run. A candidate that was merely run is not
accepted by this route, and the latest run is never assumed.
"""

from __future__ import annotations

from dataclasses import replace
import json

from fastapi import APIRouter
from starlette.datastructures import State
from starlette.requests import Request

from ..application import episodes
from ..application.binding import ProjectBinding, bound_project
from ..application.intent import DeterministicIntentProvider, component_edit_proposal
from ..application.intent_agent import DeterministicCompiler, Selection
from ..application.jobs import SUCCEEDED, Job
from ..application.projection import (
    StateProjection,
    project_state,
    require_actionable,
)
from ..application.proposals import Proposal, proposal_from
from ..transport.errors import BlockedNeedsHuman, StudioError
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
    projection = project_state(binding, run_id=body.source_run_id)
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
    proposal = replace(proposal, source_run_id=body.source_run_id)
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
    """Accept one proposal's finished candidate, turn the proposal down, or
    replace it — and keep the judgement."""

    state = request.app.state
    proposal = state.proposals.get(proposal_id)
    binding = bound_project(state)
    if body.decision == "accepted":
        if body.modified_to is not None:
            raise StudioError(
                422,
                "DECISION_INVALID",
                "an accepted decision carries no modifiedTo: accepting chooses "
                "the candidate named by candidateId, and replacing the "
                "proposal is the modified decision.",
            )
        if body.candidate_id is None:
            raise StudioError(
                422,
                "DECISION_INVALID",
                "an accepted decision has to say which run it accepts: send "
                "candidateId, a finished candidate this process ran from "
                f"proposal {proposal_id}. The latest run is never assumed.",
            )
        job = _accepted_candidate(state, proposal, body.candidate_id)
        # The evidence an acceptance cites is the accepted run's own: the
        # State Record the runner retained for that candidate, read exactly.
        # A proposal made with no sourceRunId was projected from whichever
        # run answered by default at the time, and that default can have
        # moved since — a later design run would be projected instead, and
        # its evidence written into a run that never saw it. The candidate
        # named here is the run being written into, so its record answers.
        _, record = binding.exact_state_record(
            binding.reference_run(job.candidate_id)
        )
        evidence = tuple(record.evidence_refs)
        # What is being closed with this choice is what this process still
        # holds undecided against the same base, read now — running a
        # candidate decided nothing, so a proposal that was only looked at is
        # still open here.
        superseded = episodes.still_open(
            state.episodes,
            state.proposals.for_state(proposal.base_state_digest),
            without=proposal_id,
        )
        return episode_dto(
            episodes.accept(
                state.episodes,
                binding.repository,
                binding.load_run(job.candidate_id),
                project_id=binding.project_id,
                proposal=proposal,
                superseded=superseded,
                reason=body.reason,
                evidence_refs=evidence,
                validation_refs=episodes.validation_refs_read(
                    state.jobs, state.validations, (proposal, *superseded)
                ),
            )
        )
    if body.candidate_id is not None:
        raise StudioError(
            422,
            "DECISION_INVALID",
            f"a {body.decision} decision carries no candidateId: only "
            "accepting names the run it accepts.",
        )
    # One projection answers both questions the remaining decisions ask of the
    # record: what evidence it cites, and — for a modification — what the
    # replacement sentence resolves against. Two projections could disagree.
    projection = project_state(binding, run_id=proposal.source_run_id)
    evidence = tuple(projection.record.evidence_refs)
    read = episodes.validation_refs_read(
        state.jobs, state.validations, (proposal,)
    )
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


def _accepted_candidate(
    state: State, proposal: Proposal, candidate_id: str
) -> Job:
    """The job that ran the candidate the architect is accepting, checked.

    The link is the job registry's own — ``for_candidate`` and the proposal it
    names — and it must hold three ways before anything is written: this
    process ran the candidate (an unknown one is the registry's 404), it ran
    it *from this proposal*, and the run finished as a success. A candidate
    the runner refused left no run to write a judgement into, and one still
    running has not yet shown the architect anything to accept.
    """

    job: Job = state.jobs.for_candidate(candidate_id)
    if job.proposal_id != proposal.proposal_id:
        raise StudioError(
            422,
            "DECISION_INVALID",
            f"candidate {candidate_id} was run from proposal "
            f"{job.proposal_id}, not from {proposal.proposal_id}. Accepting "
            "names a run of the proposal being accepted.",
        )
    if job.status != SUCCEEDED:
        raise StudioError(
            422,
            "DECISION_INVALID",
            f"candidate {candidate_id} is {job.status}"
            + (f": {job.error}" if job.error else "")
            + ". Only a candidate that finished successfully can be "
            "accepted; a run that never happened carries no judgement.",
        )
    return job


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

    if proposal.semantic_edit is not None:
        if (
            projection.record_digest != proposal.record_digest
            or projection.state_digest != proposal.base_state_digest
        ):
            raise StudioError(409, "STALE_BASE", "the proposal's design base has changed; read the selected run again")
        if isinstance(state.intent_compiler, DeterministicCompiler):
            raise StudioError(422, "SEMANTIC_EDIT_UNAVAILABLE", "this process has no design agent for component changes")
        compilation = state.intent_compiler.compile(
            message=(
                "Revise this unexecuted proposal against the supplied record. Return the complete revised edit.\n"
                + json.dumps(proposal.semantic_edit, ensure_ascii=False)
                + "\nArchitect's change: " + utterance
            ),
            selection=Selection(proposal.component_id, proposal.element_id),
            projection=projection,
        )
        if compilation.semantic_edit is None:
            if compilation.question:
                raise BlockedNeedsHuman(compilation.why, question=compilation.question)
            raise StudioError(422, "SEMANTIC_EDIT_INVALID", "the agent did not return the revised component edit")
        replacement = proposal_from(component_edit_proposal(
            projection, compilation.semantic_edit, utterance=utterance,
            component_id=compilation.component_id, keep_refs=proposal.protected,
        ))
        return state.proposals.put(replace(
            replacement, source_run_id=proposal.source_run_id,
            compilation_receipt=None if compilation.receipt is None else compilation.receipt.to_dict(),
            document_comment_ref=proposal.document_comment_ref,
            model_source=proposal.model_source,
        ))

    context_refs = [
        f"state:{projection.state_digest}",
        f"component:{proposal.component_id}",
    ]
    if proposal.element_id is not None:
        context_refs.append(f"element:{proposal.element_id}")
    return state.proposals.put(
        replace(
            proposal_from(
                DeterministicIntentProvider(projection).propose(
                    session_ref=f"project:{binding.project_id}",
                    message=utterance,
                    context_refs=context_refs,
                )
            ),
            source_run_id=proposal.source_run_id,
            document_comment_ref=proposal.document_comment_ref,
            model_source=proposal.model_source,
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
