"""``GET /api/state``: the authored State Record, bound and projected.

Three more read-only resources hang off it. ``GET /api/state/frame`` is the
record's frame — the levels and axes every element is positioned against —
``GET /api/state/volumes`` is its massing, which is positioned against neither
and declares its own boxes, and ``POST /api/state/closure`` answers what
changing a named ref would move. None writes; all are the kernel's own
answers, arranged.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.catalog import catalog_of
from ..application.frame import closure_of_refs, frame_of
from ..application.options import record_massing
from ..application.projection import project_state
from ..transport.errors import StudioError
from ..transport.options import VolumesDto, volumes_dto
from ..transport.state import (
    ClosureDto,
    ClosureRequestDto,
    FrameDto,
    StateProjectionDto,
    closure_dto,
    frame_dto,
    to_dto,
)

router = APIRouter(tags=["state"])


@router.get(
    "/state",
    response_model=StateProjectionDto,
    response_model_by_alias=True,
)
def read_state(
    request: Request,
    run: str | None = Query(
        default=None,
        description=(
            "Answer for this run instead of the one the rule chooses. "
            "The run must exist in the bound project."
        ),
    ),
) -> StateProjectionDto:
    """Ask the kernel; shape the answer. No design question is decided here.

    This is the one route served a record whose developed-design view the
    kernel refused: what such a record *declares* is still its own answer, and
    the refusal travels as ``componentTreeError`` and an honesty line rather
    than as a blank screen. Every route that would have to stand on that view
    refuses instead.
    """

    binding = bound_project(request.app.state)
    projection = project_state(binding, run, require_view=False)
    # The catalog stands on the bound view; a record the kernel refused to
    # view has no tree to catalogue, and the honesty line already says so.
    catalog = None if projection.state is None else catalog_of(binding, projection)
    return to_dto(projection, catalog)


@router.get(
    "/state/frame",
    response_model=FrameDto,
    response_model_by_alias=True,
)
def read_frame(request: Request) -> FrameDto:
    """The levels and axes this record positions everything against.

    Read off ``projection.record`` and nothing else, so it asks with
    ``require_view=False`` for the same reason ``GET /api/state`` does: a
    record the kernel would not build a bound view for still declares its own
    frame, and refusing to name it would withhold an answer the record gives.

    There is no ``?run=`` here on purpose. The frame is the authored record's,
    and a run changes only what the record is bound to — never which levels
    and axes it declares.
    """

    binding = bound_project(request.app.state)
    projection = project_state(binding, require_view=False)
    return frame_dto(frame_of(projection.record))


@router.get(
    "/state/volumes",
    response_model=VolumesDto,
    response_model_by_alias=True,
)
def read_volumes(request: Request) -> VolumesDto:
    """The record's massing volumes, and what the massing as a whole measures.

    Separate from the frame rather than another field of it: the frame is what
    an *element* is positioned against — a level, a grid axis — and a
    ``Volume@1`` is positioned against neither. It declares its own box in the
    massing lattice, so it is its own resource.

    ``require_view=False`` for the same reason the frame reads that way: a
    record the kernel would not build a bound view for still declares its own
    volumes.
    """

    binding = bound_project(request.app.state)
    projection = project_state(binding, require_view=False)
    return volumes_dto(record_massing(projection.record))


@router.post(
    "/state/closure",
    response_model=ClosureDto,
    response_model_by_alias=True,
)
def read_closure(request: Request, body: ClosureRequestDto) -> ClosureDto:
    """What changing these refs would move, against the state that answers now.

    A POST because the question carries a list and the state it was asked
    against; it writes nothing. The ``stateDigest`` is checked the way every
    other state-bound request checks it — an answer computed against a record
    the client was not looking at is worse than a refusal.
    """

    binding = bound_project(request.app.state)
    projection = project_state(binding)
    if body.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the request names state {body.state_digest}, but "
            f"{binding.project_id} is at {projection.state_digest}. Read "
            "/api/state again and ask against the state that answers now.",
        )
    return closure_dto(
        closure_of_refs(projection.record, tuple(body.changed_refs))
    )
