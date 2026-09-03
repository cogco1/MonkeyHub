"""``GET /api/events``: what this process is doing, as it does it.

The stream opens with a replay of what the sink still remembers and then
carries live events on the same connection. A client that reconnects sends the
last ``seq`` it saw as ``Last-Event-ID`` and is given what it missed instead of
the whole buffer again — and a resume point that cannot be read as a sequence
replays everything rather than silently skipping ahead, because guessing wrong
in that direction hides events.

The generator polls its own queue instead of blocking on it: a blocked ``get``
would hold the connection open long after the client had gone, and the run this
stream is about would go on writing to a queue nobody was reading. FastAPI
keeps the connection alive with its own ping between events.

``limit`` is the other way to read this stream: send at most that many events
and close. SSE is built to be resumed — that is what ``Last-Event-ID`` is for —
so a client that cannot hold a connection open (a terminal, a poller, a proxy
that closes long requests) asks for a bounded run of events and comes back for
the next one. Without it the stream is unbounded, which is what a browser wants.
"""

from __future__ import annotations

import asyncio
from queue import Empty
from typing import Any, AsyncIterator, Mapping

from fastapi import APIRouter, Header, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request

from ..transport.events import StudioEventDto
from ..transport.events import to_dto as event_dto

router = APIRouter(tags=["events"])

# How often the stream looks for a new event and for a client that has gone.
# Short enough that progress feels live, long enough that an idle connection
# costs almost nothing.
POLL_SECONDS = 0.05


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
        description="close the stream after this many events; a client "
        "resumes from the last one with Last-Event-ID",
    ),
) -> AsyncIterator[ServerSentEvent]:
    """Replay what this process remembers, then carry what happens next."""

    events = request.app.state.events
    sent = 0
    with events.subscribe(after=_after(last_event_id)) as (replay, inbox):
        for event in replay:
            yield _frame(event)
            sent += 1
            if sent == limit:
                return
        while not await request.is_disconnected():
            try:
                event = inbox.get_nowait()
            except Empty:
                await asyncio.sleep(POLL_SECONDS)
                continue
            yield _frame(event)
            sent += 1
            if sent == limit:
                return


def _frame(event: Mapping[str, Any]) -> ServerSentEvent:
    """One event as an SSE frame: its type, its body, and its resume point."""

    # Serialized here rather than through the response model because the frame
    # carries the id: the aliases are the wire's, and they are applied once.
    return ServerSentEvent(
        raw_data=event_dto(event).model_dump_json(by_alias=True),
        event=event["type"],
        id=str(event["seq"]),
    )


def _after(last_event_id: str | None) -> int | None:
    """The sequence a client says it already has, or None if it said nothing.

    An unreadable value is treated as nothing rather than as zero-or-worse: a
    client whose header was mangled should see the buffer again, not have the
    server decide on its behalf which events it can do without.
    """

    if last_event_id is None:
        return None
    try:
        return int(last_event_id)
    except ValueError:
        return None
