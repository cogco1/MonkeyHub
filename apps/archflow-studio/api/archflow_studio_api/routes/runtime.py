"""One read-only view for live workers and retained-result recovery."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.runtime import inspect_runtime
from ..transport.runtime import RuntimeDto, runtime_dto

router = APIRouter(tags=["runtime"])


@router.get("/runtime", response_model=RuntimeDto, response_model_by_alias=True)
def read_runtime(
    request: Request, limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    candidate_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]] = Query(default=[], alias="candidateId", max_length=200),
) -> RuntimeDto:
    return runtime_dto(inspect_runtime(bound_project(request.app.state), jobs=request.app.state.jobs,
                                       limit=limit, offset=offset, candidate_ids=tuple(candidate_ids)))
