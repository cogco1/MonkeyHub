"""The reserved ``StudioEventSink`` port, implemented for one process.

A candidate run happens on a worker thread and takes long enough that a client
which only polled would be watching a spinner. This is what it watches instead:
an ordered log of what the job did, published as it happens.

Three properties are the whole design. It is **ordered**: ``seq`` is assigned
once under a lock, so a client can say what it has already seen and be given
exactly what it missed. It is **bounded**: two hundred events, oldest dropped,
because a process nobody restarts must not grow because somebody kept asking it
to run candidates. And it is **in-process**: like the proposal store, this is
not history. It says what this service saw since it started; a restart loses
it, and nothing may ever read it as the record of what a project did. The
project's record is P036's, retained by the kernel and readable long after this
buffer has forgotten.

Subscribers get their own unbounded ``queue.Queue`` rather than sharing a
cursor into the buffer: a slow reader then falls behind on its own connection
instead of blocking the thread that is running the design work.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import queue
import threading
from typing import Any, Iterator, Mapping

# How many events one process remembers. A candidate run publishes three, so
# this is roughly the last sixty runs — long enough that a client reconnecting
# after a blip resumes, short enough to be a fixed cost.
BUFFER_SIZE = 200


class StudioEvents:
    """An ordered, bounded event log with live subscribers.

    Implements ``ports.StudioEventSink``: ``publish`` is the whole port, and
    everything else here exists to let the SSE transport read what was
    published without reaching into the buffer itself.
    """

    def __init__(self, buffer_size: int = BUFFER_SIZE) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._buffer: deque[Mapping[str, Any]] = deque(maxlen=buffer_size)
        self._subscribers: set[queue.Queue[Mapping[str, Any]]] = set()

    def publish(self, *, event: Mapping[str, Any]) -> None:
        """Stamp one event with its place in the order and hand it out.

        The caller's mapping is copied, not annotated: a publisher that reused
        a dict would otherwise find ``seq`` from a previous event still on it,
        and the second event would carry the first one's number.
        """

        stamped = {
            **event,
            "seq": 0,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._seq += 1
            stamped["seq"] = self._seq
            self._buffer.append(stamped)
            subscribers = tuple(self._subscribers)
        for inbox in subscribers:
            # Unbounded queues: a reader that has stopped reading falls behind
            # on its own connection rather than blocking the design work.
            inbox.put(stamped)

    def replay(self, after: int | None = None) -> tuple[Mapping[str, Any], ...]:
        """What this process still remembers, optionally after one sequence."""

        with self._lock:
            buffered = tuple(self._buffer)
        if after is None:
            return buffered
        return tuple(event for event in buffered if event["seq"] > after)

    @contextmanager
    def subscribe(
        self, after: int | None = None
    ) -> Iterator[
        tuple[tuple[Mapping[str, Any], ...], queue.Queue[Mapping[str, Any]]]
    ]:
        """The replay and the live queue, taken together under one lock.

        The two are handed out atomically on purpose: taking the replay first
        and subscribing afterwards would drop any event published in between,
        and subscribing first would deliver it twice.
        """

        inbox: queue.Queue[Mapping[str, Any]] = queue.Queue()
        with self._lock:
            buffered = tuple(self._buffer)
            self._subscribers.add(inbox)
        replay = (
            buffered
            if after is None
            else tuple(event for event in buffered if event["seq"] > after)
        )
        try:
            yield replay, inbox
        finally:
            with self._lock:
                self._subscribers.discard(inbox)
