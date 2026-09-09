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
from ..application.episodes import (
    add_working_copy_option, create_working_copy, list_working_copies,
    read_working_copy, select_working_copy_option,
)
from ..transport.artifacts import model_source_from
from ..transport.proposal import (
    WorkingCopyCreateRequestDto, WorkingCopyDto, WorkingCopyListDto,
    WorkingCopySelectionRequestDto, WorkingCopyOptionRequestDto,
    working_copy_dto, working_option_from,
)
from .proposals import _require_bound_project

router = APIRouter(tags=["episodes"])


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
