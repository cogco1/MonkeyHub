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

router = APIRouter(tags=["episodes"])


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
