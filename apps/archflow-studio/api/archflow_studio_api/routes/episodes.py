"""``/api/episodes``: the judgements this process has made, and where they live.

Read-only, and deliberately narrow. A judgement is *written* by deciding — a
rejection or a modification through ``POST /api/proposals/{id}/decision``, an
acceptance by running the proposal as a candidate — and never by asking for one
here. What this route adds is the ability to see a judgement that has not met a
run yet, which is the whole reason the in-memory half exists.

Every episode says where it lives in its own ``persistence`` field: ``run:<id>``
once it is in a run's records, and the in-memory sentence while it is only this
process's. A client that shows the second has been told the truth about what it
is looking at, exactly as it is for a proposal.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..transport.proposal import EpisodeDto, episode_dto
from ..application.binding import bound_project
from ..application.design_history import (
    accept_design_candidate, fork_design_branch, initialize_design_stage,
    read_design_history, stage_ref_from,
)
from ..application.episodes import (
    add_working_copy_option, create_working_copy, list_working_copies,
    read_working_copy, select_working_copy_option,
)
from ..transport.artifacts import model_source_from
from ..transport.design_history import (
    AcceptDesignCandidateRequestDto, DesignBranchDto, DesignHistoryDto, DesignStageDto,
    ForkDesignBranchRequestDto, InitializeDesignStageRequestDto,
    branch_dto, history_dto, stage_dto,
)
from ..transport.proposal import (
    WorkingCopyCreateRequestDto, WorkingCopyDto, WorkingCopyListDto,
    WorkingCopySelectionRequestDto, WorkingCopyOptionRequestDto,
    working_copy_dto, working_option_from,
)
from .proposals import _require_bound_project

router = APIRouter(tags=["episodes"])


@router.get("/design-history", response_model=DesignHistoryDto, response_model_by_alias=True)
def read_committed_design_history(request: Request, branch_id: str = Query(default="main", alias="branchId")) -> DesignHistoryDto:
    return history_dto(read_design_history(bound_project(request.app.state), branch_id))


@router.post("/design-stages/initialize", response_model=DesignStageDto, response_model_by_alias=True, status_code=201)
def initialize_committed_design(request: Request, payload: InitializeDesignStageRequestDto) -> DesignStageDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return stage_dto(initialize_design_stage(binding, model_source=model_source_from(payload.model_source),
                                            branch_id=payload.branch_id, label=payload.label))


@router.post("/candidates/{candidate_id}/accept", response_model=DesignStageDto, response_model_by_alias=True)
def accept_committed_design(request: Request, candidate_id: str, payload: AcceptDesignCandidateRequestDto) -> DesignStageDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return stage_dto(accept_design_candidate(binding, candidate_id=candidate_id, branch_id=payload.branch_id,
                                            expected_head=stage_ref_from(binding, payload.expected_head_stage_ref),
                                            events=request.app.state.events, label=payload.label))


@router.post("/design-branches", response_model=DesignBranchDto, response_model_by_alias=True, status_code=201)
def fork_committed_design(request: Request, payload: ForkDesignBranchRequestDto) -> DesignBranchDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return branch_dto(fork_design_branch(binding, branch_id=payload.branch_id, parent_branch=payload.parent_branch,
                                         stage_ref=stage_ref_from(binding, payload.stage_ref)))


@router.get("/working-copies", response_model=WorkingCopyListDto, response_model_by_alias=True)
def read_working_copies(request: Request) -> WorkingCopyListDto:
    return WorkingCopyListDto(workingCopies=[working_copy_dto(item) for item in list_working_copies(bound_project(request.app.state))])


@router.get("/working-copies/{group_id}", response_model=WorkingCopyDto, response_model_by_alias=True)
def read_working_copy_group(request: Request, group_id: str, revision_sha256: str | None = Query(default=None, alias="revisionSha256")) -> WorkingCopyDto:
    return working_copy_dto(read_working_copy(bound_project(request.app.state), group_id, revision_sha256))


@router.post("/working-copies", response_model=WorkingCopyDto, response_model_by_alias=True, status_code=201)
def create_working_copy_group(request: Request, payload: WorkingCopyCreateRequestDto) -> WorkingCopyDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return working_copy_dto(create_working_copy(binding, payload.group_id, payload.label, payload.stage_id,
                                              model_source_from(payload.common_base), payload.scope,
                                              [working_option_from(option) for option in payload.options]))


@router.put("/working-copies/{group_id}/selection", response_model=WorkingCopyDto, response_model_by_alias=True)
def choose_working_copy_option(request: Request, group_id: str, payload: WorkingCopySelectionRequestDto) -> WorkingCopyDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return working_copy_dto(select_working_copy_option(binding, group_id, payload.base_revision_sha256, payload.option_id))


@router.post("/working-copies/{group_id}/options", response_model=WorkingCopyDto, response_model_by_alias=True)
def append_working_copy_option(request: Request, group_id: str, payload: WorkingCopyOptionRequestDto) -> WorkingCopyDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return working_copy_dto(add_working_copy_option(binding, group_id, payload.base_revision_sha256,
                                                   working_option_from(payload.option), event_sink=request.app.state.events))


@router.get(
    "/episodes",
    response_model=list[EpisodeDto],
    response_model_by_alias=True,
)
def list_episodes(
    request: Request,
    state_digest: str | None = Query(
        None,
        alias="stateDigest",
        description="only the judgements made against this exact state; "
        "left out, every judgement this process holds",
    ),
) -> list[EpisodeDto]:
    """Every judgement this process holds, in the order it made them."""

    return [
        episode_dto(episode)
        for episode in request.app.state.episodes.for_state(state_digest)
    ]


@router.get(
    "/episodes/{episode_id}",
    response_model=EpisodeDto,
    response_model_by_alias=True,
)
def read_episode(request: Request, episode_id: str) -> EpisodeDto:
    """One judgement, or a 404 that says where judgements do not survive."""

    return episode_dto(request.app.state.episodes.get(episode_id))
