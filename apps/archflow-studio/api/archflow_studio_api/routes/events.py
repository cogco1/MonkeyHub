"""``GET /api/events``: what this process is doing, as it does it.

The stream opens with a replay of what the sink still remembers and then
carries live events on the same connection. Each frame's id is
``<stream>:<seq>``: this process's stream and its place in it. A client that
reconnects sends the last one it saw as ``Last-Event-ID`` and is given what it
missed instead of the whole buffer again. A resume point this process cannot
honour - another process's (a restart), one without a stream, or one older
than the buffer (an overflow) - is answered with a ``stream.reset`` frame and
the whole buffer, never by silently skipping ahead: the client learns it may
have missed events and must read again (#366).

It also carries ``index.committed``: the project index moved (a hint only;
``GET /api/index?since=`` is how a client catches up).

The generator polls its own queue instead of blocking on it: a blocked ``get``
would hold the connection open long after the client had gone, and the run this
stream is about would go on writing to a queue nobody was reading. FastAPI
keeps the connection alive with its own ping between events.

``limit`` is the other way to read this stream: catch up on what is already
there and close. It sends at most that many events — replay included — and
stops as soon as there is nothing left to send, so a bounded read **always**
terminates. That second half matters as much as the first: a limit that waited
for its quota would hang forever on a quiet process, which is exactly the trap
a client asking for a bounded read is trying to avoid. SSE is built to be
resumed — that is what ``Last-Event-ID`` is for — so such a client reads what
is there and comes back for the rest. Without ``limit`` the stream is unbounded
and waits for live events, which is what a browser wants.
"""

from __future__ import annotations

import asyncio
from queue import Empty
from typing import Any, AsyncIterator, Mapping

from fastapi import APIRouter, Header, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request

from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError

from ..application.binding import bound_project
from ..transport.errors import StudioError
from ..transport.events import ClientTimingDto, ModelLoadTimingDto, MonitorWriteDto, StudioEventDto
from ..transport.events import to_dto as event_dto
from ..transport.index import IndexEventDto

router = APIRouter(tags=["events"])

# How often the stream looks for a new event and for a client that has gone.
# Short enough that progress feels live, long enough that an idle connection
# costs almost nothing.
POLL_SECONDS = 0.05


@router.post("/events/model-load", response_model=MonitorWriteDto)
def record_model_load(request: Request, payload: ModelLoadTimingDto) -> MonitorWriteDto:
    """Record browser wait separately from service and model-call durations."""

    state = request.app.state
    if state.settings.monitor_dir is None:
        return MonitorWriteDto(recorded=False)
    binding = bound_project(state)
    if payload.project_id != binding.project_id:
        raise StudioError(409, "PROJECT_MISMATCH", "This measurement names another project.")
    binding.load_run(payload.run_id)
    if payload.source_ref is not None:
        try:
            ref = record_ref_from_uri(payload.source_ref, binding.project_id)
            layout = binding.repository.layout
            if not layout.resolve_relative(ref.relative_path).is_relative_to(layout.run(payload.run_id).root):
                raise ValueError("record belongs to another run")
            binding.repository.load_json(ref)
        except (ValueError, OSError, ProjectRepositoryError) as exc:
            raise StudioError(409, "SOURCE_MISMATCH", "The measurement source is not retained by this run.") from exc
    event_id = state.monitor.record(
        event_id=f"studio:client:{payload.event_id}",
        phase="model_load", timing_scope="client_wait", status=payload.status,
        started_at=payload.started_at.isoformat(), ended_at=payload.ended_at.isoformat(),
        duration_ms=payload.duration_ms, project_id=binding.project_id, run_id=payload.run_id,
        source_ref=payload.source_ref,
        related_event_id=f"studio:candidate:{binding.project_id}:{payload.run_id}",
    )
    return MonitorWriteDto(recorded=event_id is not None)


@router.post("/events/timing", response_model=MonitorWriteDto)
def record_client_timing(request: Request, payload: ClientTimingDto) -> MonitorWriteDto:
    state = request.app.state
    if state.settings.monitor_dir is None:
        return MonitorWriteDto(recorded=False)
    binding = bound_project(state)
    if payload.project_id != binding.project_id:
        raise StudioError(409, "PROJECT_MISMATCH", "This measurement names another project.")
    if payload.run_id is not None:
        try:
            binding.load_run(payload.run_id)
        except (StudioError, ValueError, OSError, ProjectRepositoryError):
            # A failed or queued job may not yet have created its retained run.
            state.jobs.for_candidate(payload.run_id)
    if payload.source_ref is not None:
        try:
            ref = record_ref_from_uri(payload.source_ref, binding.project_id)
            binding.repository.load_json(ref)
        except (ValueError, OSError, ProjectRepositoryError) as exc:
            raise StudioError(409, "SOURCE_MISMATCH", "The measurement input is not retained by this project.") from exc
    details = payload.details.model_dump(exclude_none=True)
    asset_sha = details.pop("asset_sha256", None)
    if asset_sha is not None:
        details["input_identity"] = {"asset_sha256": asset_sha}
    event_id = state.monitor.record(
        event_id=f"studio:client:{payload.event_id}", operation_id=f"studio:client:{payload.operation_id}",
        parent_event_id=f"studio:client:{payload.parent_event_id}" if payload.parent_event_id else None,
        phase=payload.phase, timing_scope="interaction" if payload.phase == "design_edit" else "client_wait",
        status=payload.status, started_at=payload.started_at.isoformat(),
        ended_at=payload.ended_at.isoformat() if payload.ended_at else None, duration_ms=payload.duration_ms,
        project_id=binding.project_id, run_id=payload.run_id, source_ref=payload.source_ref, details=details,
        related_event_id=f"studio:candidate:{binding.project_id}:{payload.run_id}" if payload.run_id else None,
    )
    return MonitorWriteDto(recorded=event_id is not None)


@router.get(
    "/events",
    response_model=StudioEventDto,
    response_model_by_alias=True,
    response_class=EventSourceResponse,
)
async def stream_events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    limit: int | None = Query(
        default=None,
        ge=1,
        description="catch-up read: send at most this many events, replay "
        "included, and close as soon as there is nothing left to send. A "
        "client resumes from the last one with Last-Event-ID. Omit it to "
        "hold the connection open and wait for live events.",
    ),
) -> AsyncIterator[ServerSentEvent]:
    """Replay what this process remembers, then carry what happens next."""

    events = request.app.state.events
    sent = 0
    after, reset = events.resume(last_event_id)
    with events.subscribe(after=after) as (replay, inbox):
        if reset is not None:
            # No id: it resumes nothing, and the replay after it carries the ids.
            yield ServerSentEvent(raw_data=IndexEventDto(seq=0, at="", type="stream.reset", reason=reset)
                                  .model_dump_json(by_alias=True, exclude_none=True), event="stream.reset")
        for event in replay:
            yield _frame(event, events.stream)
            sent += 1
            if sent == limit:
                return
        while request.app.state.jobs.accepting and not await request.is_disconnected():
            try:
                event = inbox.get_nowait()
            except Empty:
                if limit is not None:
                    # A bounded read is a catch-up, not a wait: the backlog is
                    # drained, so this connection is done even though it sent
                    # fewer events than it was allowed to.
                    return
                await asyncio.sleep(POLL_SECONDS)
                continue
            yield _frame(event, events.stream)
            sent += 1
            if sent == limit:
                return


def _frame(event: Mapping[str, Any], stream: str) -> ServerSentEvent:
    """One event as an SSE frame: its type, its body, and its resume point."""

    # Serialized here rather than through the response model because the frame
    # carries the id: the aliases are the wire's, and they are applied once.
    if event["type"] == "index.committed":
        body = IndexEventDto(seq=event["seq"], at=event["at"], type=event["type"], epoch=event["epoch"],
                             revision=event["revision"], domains=list(event["domains"]))
        data = body.model_dump_json(by_alias=True, exclude_none=True)
    else:
        data = event_dto(event).model_dump_json(by_alias=True)
    return ServerSentEvent(raw_data=data, event=event["type"], id=f"{stream}:{event['seq']}")
