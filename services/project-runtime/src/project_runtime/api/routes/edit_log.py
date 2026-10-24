"""What was changed along a line of runs, read from the retained changes; a guess rule's input (#300)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application.edit_log import by_target, edit_log
from ...application.working_draft import resolve_working_source
from ...binding import bound_project
from ...errors import StudioError
from ..dto.edit_log import EditLogDto, edit_log_dto

router = APIRouter(tags=["edit-log"])


@router.get("/edit-log", response_model=EditLogDto, response_model_by_alias=True)
def read_edit_log(
    request: Request,
    run_id: str | None = Query(default=None, alias="run", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> EditLogDto:
    """Read-only: the changes along ``run``'s line (the Working Head's by default), nearest first.

    A guess rule reads its last few steps with a small ``limit``, so a change
    retained long ago that can no longer be read keeps no rule from speaking.
    """

    binding = bound_project(request.app.state)
    if run_id is None:
        head = resolve_working_source(binding).head
        run_id = None if head is None else head.run_id
    elif run_id not in binding.run_ids():
        raise StudioError(404, "RUN_NOT_FOUND", f"The project has no run {run_id}.")
    steps = () if run_id is None else edit_log(binding, run_id, limit=limit)
    return edit_log_dto(run_id, steps, by_target(steps))
