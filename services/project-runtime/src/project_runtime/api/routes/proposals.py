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
judgement is written into that run, the other options remain available,
and the judgements held until then
against that base go into the same run. A candidate that was merely run is not
accepted by this route, and the latest run is never assumed.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter
from pydantic import ValidationError
from starlette.datastructures import State
from starlette.requests import Request

from archflow.project.repository import ProjectRepositoryError
from archflow.state.state_record import StateRecordError, apply_state_record_operator

from ...application import clarification, episodes
from ...authentication import request_attribution
from ...application.construction import kept_refs
from ...application.elevation import elevation_proposal
from ...binding import ProjectBinding, bound_project
from ...application.seats import SeatsError, load_seat_pack, seats_of
from ...application.intent import (
    DeterministicIntentProvider,
    buildable_components,
    component_edit_proposal,
    compress_above_proposal,
    parameter_locks_proposal,
    merge_keep,
    direct_element_proposal,
    delete_element_proposal,
    sketch_prism_proposal,
)
from ...application.intent_agent import DeterministicCompiler, Selection, context_refs as compiled_context_refs
from ...jobs import SUCCEEDED, Job
from ...application.projection import (
    StateProjection,
    project_state,
    project_proposed_record,
    require_actionable,
)
from ...application.proposals import Proposal, continue_proposal, operator_of, proposal_from, sentences_of
from ...application.gestures import DocumentAnnotationRef, read_document_tracing
from ...errors import BlockedNeedsHuman, StudioError
from ..dto.proposal import (
    EpisodeDto,
    ProposalDecisionRequestDto,
    ProposalDto,
    ProposalRequestDto,
    ParameterLocksRequestDto,
    DeleteElementRequestDto,
    SketchPrismRequestDto,
    SketchActionDto,
    SketchBatchRequestDto,
    DocumentTracingRequestDto,
    TransformElementRequestDto,
    PushPullRequestDto,
    ElevationEditRequestDto,
    episode_dto,
    to_dto,
)

router = APIRouter(tags=["proposals"])

# How many runs a stale refusal reads, newest first, for the one whose state was sent.
_STALE_SCAN_RUNS = 200


def _run_with_state(binding: ProjectBinding, digest: str) -> tuple[str | None, int, int]:
    """The run whose newest receipt carries ``digest``, and how many runs were read of how many.

    Only a refusal asks, so only the error path pays: runs are read newest first
    by their directory's time, and the reading stops at the first match or after
    ``_STALE_SCAN_RUNS``. A run that cannot be read is passed over, not an error.
    """

    runs = binding.repository.layout.runs
    dated: list[tuple[float, str]] = []
    for run_id in binding.run_ids():
        try:
            dated.append(((runs / run_id).stat().st_mtime, run_id))
        except OSError:
            continue
    dated.sort(reverse=True)
    read = dated[:_STALE_SCAN_RUNS]
    for _mtime, run_id in read:
        try:
            newest = binding.newest_runner_receipt(run_id)
        except (StudioError, ProjectRepositoryError, ValueError, OSError):
            continue
        if newest is not None and newest[1].get("design_state_digest") == digest:
            return run_id, len(read), len(dated)
    return None, len(read), len(dated)


def _stale_base(binding: ProjectBinding, projection: StateProjection, sent: str, action: str,
                source_run_id: str | None) -> StudioError:
    """``STALE_BASE`` naming the source that was checked and, when a run holds it, the state that was sent (#404 F5).

    Without ``sourceRunId`` a write is checked against the project's default
    source, which never holds a candidate the caller made. "Read /api/state
    again" alone led back to that default; the refusal names the run to send.
    """

    if source_run_id is not None:
        checked = f"run {source_run_id}"
    elif projection.reference.source == "none":
        checked = "the project's authored base (no sourceRunId was sent)"
    else:
        checked = f"the project's default source, run {projection.run.run_id} (no sourceRunId was sent)"
    detail = f"{action} names state {sent}, but {checked} is at {projection.state_digest}."
    owner, read, total = _run_with_state(binding, sent)
    if owner is not None:
        return StudioError(409, "STALE_BASE", detail + f" That state is run {owner}'s: read GET /api/construction/model?run={owner} "
                           f'(or /api/state?run={owner}) and send sourceRunId "{owner}" with its stateDigest.')
    searched = "No retained run" if read == total else f"None of the newest {read} of {total} runs"
    return StudioError(409, "STALE_BASE", detail + f" {searched} has that state: read GET /api/construction/model?run=<runId> "
                       "(or /api/state?run=<runId>) for the run you are changing and send its stateDigest with sourceRunId.")


def _proposal_source(request: Request, body):
    """Resolve a retained base once and, optionally, its unexecuted proposal."""

    binding = bound_project(request.app.state)
    _require_bound_project(binding, body.project_id)
    previous = (
        None if body.source_proposal_id is None
        else request.app.state.proposals.get(body.source_proposal_id)
    )
    if previous is not None:
        if previous.status == "conflict":
            raise StudioError(409, "PROPOSAL_NOT_RUNNABLE", "Resolve the source proposal's keep conflict before continuing it.")
        stage_ref = None if previous.source_stage_ref is None else previous.source_stage_ref.uri
        if ((body.source_run_id is not None and body.source_run_id != previous.source_run_id)
                or (body.source_stage_ref is not None and body.source_stage_ref != stage_ref)):
            raise StudioError(409, "PROPOSAL_SOURCE_MISMATCH", "A proposal chain must keep its original run and Stage base.")
        body = body.model_copy(update={
            "source_run_id": previous.source_run_id, "source_stage_ref": stage_ref,
        })
    base = project_state(binding, run_id=body.source_run_id, source_stage_ref=body.source_stage_ref)
    require_actionable(base)
    if previous is None:
        return binding, base, base, previous, body
    if (body.state_digest != base.state_digest or previous.base_state_digest != base.state_digest
            or previous.record_digest != base.record_digest):
        stale = _stale_base(binding, base, body.state_digest, "the proposal chain continuation", body.source_run_id)
        moved = previous.base_state_digest != base.state_digest or previous.record_digest != base.record_digest
        raise StudioError(409, "STALE_BASE", stale.detail + (
            f" A continuation sends its chain's own baseStateDigest {previous.base_state_digest}"
            + (", and that source has moved since: start a new chain from its current state." if moved else ".")))
    record = apply_state_record_operator(base.record, operator_of(previous, base.record))
    projection = project_proposed_record(base, record)
    return binding, base, projection, previous, body.model_copy(update={"state_digest": projection.state_digest})


def _remember_proposal(request: Request, proposal: Proposal, base: StateProjection, previous: Proposal | None) -> ProposalDto:
    if previous is not None:
        proposal = continue_proposal(base, previous, proposal)
    return to_dto(request.app.state.proposals.put(proposal))


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

    binding, base, projection, previous, body = _proposal_source(request, body)
    if body.state_digest != projection.state_digest:
        # The utterance path (and the capability run through it) answers as every other write does.
        raise _stale_base(binding, projection, body.state_digest,
                          "the semantic edit" if body.semantic_edit is not None else "the proposal", body.source_run_id)
    if body.semantic_edit is not None:
        proposal = proposal_from(component_edit_proposal(
            projection, body.semantic_edit.model_dump(by_alias=True),
            utterance=body.semantic_edit.summary, component_id=body.target_component_id,
            keep_refs=tuple(body.keep),
        ))
    else:
        proposal = proposal_from(DeterministicIntentProvider(projection).propose(
            # Round 1 has no session identity: the project the request is bound
            # to is what answers for it, and nothing about who asked changes
            # what the record says.
            session_ref=f"project:{binding.project_id}",
            message=merge_keep(body.utterance, body.keep),
            context_refs=body.context_refs(),
        ))
    proposal = replace(proposal, source_run_id=projection.run.run_id if projection.reference_state_exact else body.source_run_id,
                       source_stage_ref=projection.source_stage_ref)
    return _remember_proposal(request, proposal, base, previous)


@router.post("/proposals/parameter-locks", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def create_parameter_locks_proposal(request: Request, body: ParameterLocksRequestDto) -> ProposalDto:
    """Authorize via the Runtime boundary, then compile on the exact retained state.

    Configured actors require the existing accept grant. In unauthenticated
    local mode attribution names the local boundary, not an identified person.
    """
    binding = bound_project(request.app.state)
    _require_bound_project(binding, body.project_id)
    projection = project_state(binding, run_id=body.source_run_id, source_stage_ref=body.source_stage_ref)
    require_actionable(projection)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the parameter lock action", body.source_run_id)
    proposal = proposal_from(parameter_locks_proposal(
        projection, parameter_keys=tuple(body.parameter_keys),
        lock_authority=request_attribution(request).actor_id if body.action == "lock" else None,
    ))
    proposal = replace(proposal,
                       source_run_id=projection.run.run_id if projection.reference_state_exact else body.source_run_id,
                       source_stage_ref=projection.source_stage_ref)
    return to_dto(request.app.state.proposals.put(proposal))


@router.post(
    "/proposals/sketch",
    response_model=ProposalDto,
    response_model_by_alias=True,
    status_code=201,
)
def create_sketch_proposal(
    request: Request, body: SketchPrismRequestDto | SketchBatchRequestDto | DocumentTracingRequestDto
) -> ProposalDto:
    """A finished drawing action becomes the same proposal a sentence would.

    One request per completed action, never per pointer move: the preview
    lives in the browser and only the profile and height that were settled
    arrive here. What comes back is an ordinary proposal, so running it is the
    candidate route that already exists and nothing new executes anything.
    """

    binding, base, projection, previous, body = _proposal_source(request, body)
    if body.state_digest != projection.state_digest:
        # Drawn against one exact state, like every other proposal here.
        raise _stale_base(binding, projection, body.state_digest, "the drawing", body.source_run_id)
    if isinstance(body, DocumentTracingRequestDto):
        ref = body.tracing
        rows = read_document_tracing(binding, DocumentAnnotationRef(
            ref.run_id, ref.asset_sha256, ref.page_index, ref.revision_sha256, ref.drawing_revision_ref,
        ), ref.annotation_ids)
        if body.height == 0 and any(row["closed"] for row in rows):
            raise StudioError(422, "TRACING_HEIGHT_REQUIRED", "Choose a positive height for the selected closed contours.")
        try:
            actions = [(SketchActionDto(
                component_id=body.component_id, parent_component_id=body.parent_component_id,
                element_id=row["elementId"], profile=row["profile"],
                closed=row["closed"], height=body.height if row["closed"] else 0,
                base_level=body.base_level, base_datum=body.base_datum, summary=body.summary,
            ), row["sourceDocumentTrace"]) for row in rows]
        except ValidationError as exc:
            raise StudioError(422, "TRACING_PROFILE_INVALID", "The calibrated path has repeated or invalid vertices. Correct the saved path before generating again.") from exc
    else:
        actions = [(action, None) for action in (body.sketches if isinstance(body, SketchBatchRequestDto) else [body])]
    proposal = previous
    for action, trace in actions:
        step = _sketch_proposal(binding, projection, action, tuple(body.keep), source_document_trace=trace)
        step = replace(
            step, source_run_id=body.source_run_id or (base.run.run_id if base.reference_state_exact else None),
            source_stage_ref=base.source_stage_ref,
        )
        proposal = step if proposal is None else continue_proposal(base, proposal, step)
        if proposal.status == "conflict":
            # A refused batch never leaves an executable prefix in the store.
            if isinstance(body, (SketchBatchRequestDto, DocumentTracingRequestDto)):
                raise StudioError(409, "PROPOSAL_CHAIN_CONFLICT", "The sketch batch reaches protected refs: " + ", ".join(proposal.impact.conflicts))
            break
        projection = project_proposed_record(base, apply_state_record_operator(base.record, operator_of(proposal, base.record)))
    assert proposal is not None
    if isinstance(body, (SketchBatchRequestDto, DocumentTracingRequestDto)) and body.summary is not None:
        proposal = replace(proposal, utterance=body.summary,
                           semantic_edit={**proposal.semantic_edit, "summary": body.summary},
                           # A summarized batch is one step of its chain (#404 F17).
                           steps=() if previous is None else (*sentences_of(previous), body.summary))
    return to_dto(request.app.state.proposals.put(proposal))


def _sketch_proposal(binding: ProjectBinding, projection: StateProjection,
                     body: SketchActionDto, keep: tuple[str, ...], *, source_document_trace: dict | None = None) -> Proposal:
    # Which components a seat will actually build. A drawing under any other
    # one would be carried by the record and built by nobody, so it is refused
    # here — with the list — rather than queued into a run that reports success
    # and exports nothing of what was asked for.
    try:
        seats = seats_of(load_seat_pack(binding.repository))
    except SeatsError as exc:
        raise StudioError(422, "SEATS_UNAVAILABLE", str(exc)) from exc
    buildable = buildable_components(projection, seats)
    target = body.parent_component_id or body.component_id
    if target not in buildable:
        raise StudioError(
            422,
            "COMPONENT_NOT_BUILT",
            f"no seat builds {target}: draw under one of {list(buildable)}, "
            "or have the project's seat pack own it. Nothing was run.",
        )
    if source_document_trace is not None:
        existing = next((entity for entity in projection.record.entities if entity.entity_id == body.element_id), None)
        identity_keys = ("runId", "assetSha256", "pageIndex", "drawingRevisionRef", "annotationId")
        if existing is not None and (
            existing.schema != "Element@1" or existing.parent_id != body.component_id
            or not isinstance(existing.fields.get("sourceDocumentTrace"), dict)
            or any(existing.fields["sourceDocumentTrace"].get(key) != source_document_trace.get(key) for key in identity_keys)
        ):
            raise StudioError(409, "TRACING_TARGET_MISMATCH", "This traced path already belongs to a different model object or component. Its existing object was preserved.")
    proposal = proposal_from(
        sketch_prism_proposal(
            projection,
            component_id=body.component_id,
            element_id=body.element_id,
            profile=body.profile,
            height=body.height,
            closed=body.closed,
            base=body.base_reference(),
            plane=body.plane.model_dump(by_alias=True) if body.plane is not None else None,
            parent_component_id=body.parent_component_id,
            summary=body.summary,
            keep_refs=keep,
            source_document_trace=source_document_trace,
        )
    )
    return proposal


def _direct_proposal(request: Request, body: TransformElementRequestDto | PushPullRequestDto,
                     kind: str, **action) -> ProposalDto:
    binding, base, projection, previous, body = _proposal_source(request, body)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the modeling action", body.source_run_id)
    if kind == "compress-above":
        proposal = proposal_from(compress_above_proposal(
            projection, component_id=body.component_id, element_ids=body.element_ids or (),
            threshold=body.threshold, factor=body.factor, keep_refs=tuple(body.keep)))
    else:
        proposal = proposal_from(direct_element_proposal(
            projection, element_id=body.element_id, kind=kind, keep_refs=tuple(body.keep), **action))
    proposal = replace(proposal,
                       source_run_id=projection.run.run_id if projection.reference_state_exact else body.source_run_id,
                       source_stage_ref=projection.source_stage_ref)
    return _remember_proposal(request, proposal, base, previous)


@router.post("/proposals/transform", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def create_transform_proposal(request: Request, body: TransformElementRequestDto) -> ProposalDto:
    """Transform one element, or compress selected planar-surfaces above a fixed height in one proposal."""

    return _direct_proposal(request, body, body.kind, translation=body.translation, axis=body.axis,
                            angle_degrees=body.angle_degrees, scale=body.scale, origin=body.origin,
                            copy_element_id=body.copy_element_id)


@router.post("/proposals/push-pull", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def create_push_pull_proposal(request: Request, body: PushPullRequestDto) -> ProposalDto:
    """Read the exact element definition and move its selected profile end face."""

    return _direct_proposal(request, body, "push_pull", distance=body.distance, normal=body.normal)


@router.post("/proposals/elevation", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def create_elevation_proposal(request: Request, body: ElevationEditRequestDto) -> ProposalDto:
    """Edit a prism's elevation or a simple datum through the same exact-base candidate chain."""

    binding, base, projection, previous, body = _proposal_source(request, body)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the elevation action", body.source_run_id)
    proposal = proposal_from(elevation_proposal(
        projection, action=body.action, element_id=body.element_id, value=body.value,
        reference=None if body.reference is None else body.reference.model_dump(),
        level_id=body.level_id, name=body.name, keep_refs=tuple(body.keep),
    ))
    proposal = replace(proposal,
                       source_run_id=projection.run.run_id if projection.reference_state_exact else body.source_run_id,
                       source_stage_ref=projection.source_stage_ref)
    return _remember_proposal(request, proposal, base, previous)


@router.post(
    "/proposals/delete",
    response_model=ProposalDto,
    response_model_by_alias=True,
    status_code=201,
)
def create_delete_proposal(
    request: Request, body: DeleteElementRequestDto
) -> ProposalDto:
    """One picked element removed, as an ordinary proposal at one exact base.

    The Delete key is a design edit, so it arrives the way every other design
    edit does and runs through the candidate route that already exists. It
    compiles nothing with a model: a keystroke that quietly spent a model call
    would be a different thing from what the architect pressed.
    """

    binding, base, projection, previous, body = _proposal_source(request, body)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the delete", body.source_run_id)
    proposal = proposal_from(
        delete_element_proposal(
            projection,
            element_id=body.element_id,
            summary=body.summary,
            keep_refs=tuple(body.keep),
        )
    )
    proposal = replace(
        proposal,
        source_run_id=projection.run.run_id if projection.reference_state_exact else body.source_run_id,
        source_stage_ref=projection.source_stage_ref,
    )
    return _remember_proposal(request, proposal, base, previous)


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
        return episode_dto(
            episodes.accept(
                state.episodes,
                binding.repository,
                binding.load_run(job.candidate_id),
                project_id=binding.project_id,
                proposal=proposal,
                reason=body.reason,
                evidence_refs=evidence,
                validation_refs=episodes.validation_refs_read(
                    state.jobs, state.validations, (proposal,)
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
    projection = project_state(binding, run_id=proposal.source_run_id, source_stage_ref=proposal.source_stage_ref)
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
        # The agent reads the design with the proposal applied, in construction
        # terms, and answers the change to make on top of it; the replacement is
        # that change folded onto the same exact base. It is shown no record
        # row: the proposal travels as its summary and the model it would make.
        # That is the design the change would make, whatever it was asked to
        # keep: a keep conflict stays one, reviewable, on the replacement.
        try:
            proposed = project_proposed_record(projection, apply_state_record_operator(
                projection.record, replace(operator_of(proposal, projection.record), protected=())))
        except StateRecordError as exc:
            raise StudioError(409, "PROPOSAL_CHAIN_CONFLICT", str(exc)) from exc
        # A proposal that removes what it keeps has no design to change on top
        # of: that is its own keep conflict, never a question to the architect.
        declared = ({entity.ref for entity in proposed.record.entities}
                    | {parameter.ref for parameter in proposed.record.parameters})
        removed = [ref for ref in proposal.protected if ref not in declared]
        if removed:
            raise StudioError(
                409, "PROPOSAL_CHAIN_CONFLICT",
                f"proposal {proposal.proposal_id} removes {', '.join(removed)}, which it keeps: take "
                f"{'it' if len(removed) == 1 else 'them'} out of keep, or propose a change that does not remove "
                f"{'it' if len(removed) == 1 else 'them'}",
            )
        summary = str(proposal.semantic_edit.get("summary") or proposal.utterance)
        compilation = state.intent_compiler.compile(
            message=(
                "The sheet shows the design with an unexecuted proposal already applied. It proposes: "
                + summary + "\nAnswer the change the architect now asks for, made on top of it.\n"
                + "Architect's change: " + utterance
            ),
            selection=Selection(proposal.component_id, proposal.element_id),
            projection=proposed,
        )
        compilation = clarification.targeted(proposed, compilation)
        if compilation.status == "question":
            raise BlockedNeedsHuman(compilation.why, question=compilation.question or "")
        if compilation.status != "compiled":
            raise StudioError(422, "SEMANTIC_EDIT_INVALID",
                              compilation.why or "the agent did not return a change to the proposal")
        if compilation.proposes_change:
            revision = clarification.compiled_proposal(
                binding, proposed, compilation, utterance=utterance, keep_refs=proposal.protected,
                component_id=proposal.component_id,
            ).proposal
        else:
            revision = proposal_from(DeterministicIntentProvider(proposed).propose(
                session_ref=f"project:{binding.project_id}",
                message=merge_keep(compilation.utterance or "",
                                   kept_refs(proposed.record, (*proposal.protected, *compilation.keep))),
                context_refs=compiled_context_refs(
                    proposed.state_digest or "", compilation,
                    Selection(proposal.component_id, proposal.element_id),
                ),
            ))
        replacement = continue_proposal(projection, proposal, revision)
        return state.proposals.put(replace(
            replacement, utterance=utterance, pending=None,
            source_run_id=proposal.source_run_id, source_stage_ref=proposal.source_stage_ref,
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
            source_stage_ref=proposal.source_stage_ref,
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
