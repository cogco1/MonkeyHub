"""The wire form of one progress event.

``seq`` is the whole contract with a client that reconnects: it is the id on
the SSE frame, and the number the browser sends back as ``Last-Event-ID``. The
rest is a candidate's identity restated on every event, because a stream is not
a conversation — a client that joined late, or resumed, must be able to read
one frame and know which job and which run it is about without having seen the
frames before it.

Fields a given event has nothing to say about travel as ``null`` rather than
being omitted, so the shape a client parses is the same on all four types.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class StudioEventDto(BaseModel):
    """One event on ``GET /api/events``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    seq: int = Field(
        description="this process's ordering; send it back as Last-Event-ID "
        "to resume after it",
    )
    at: str = Field(description="when the event was published, UTC ISO-8601")
    type: str = Field(
        description="candidate.queued | candidate.running | "
        "candidate.succeeded | candidate.failed | validation.computed",
    )
    job_id: str | None = Field(alias="jobId", default=None)
    candidate_id: str | None = Field(alias="candidateId", default=None)
    proposal_id: str | None = Field(alias="proposalId", default=None)
    run_id: str | None = Field(alias="runId", default=None)
    wall_time_s: float | None = Field(alias="wallTimeS", default=None)
    error: str | None = Field(default=None)
    advance: bool | None = Field(
        default=None,
        description="on validation.computed, the server's verdict; null on "
        "the lifecycle events, which decide nothing",
    )
    blocked_by: list[str] | None = Field(
        alias="blockedBy",
        default=None,
        description="on validation.computed, the clauses that refused. An "
        "empty list is a real answer and is not the same as null",
    )


def to_dto(event: Mapping[str, Any]) -> StudioEventDto:
    """Shape one published event for the wire."""

    return StudioEventDto(
        seq=event["seq"],
        at=event["at"],
        type=event["type"],
        job_id=event.get("job_id"),
        candidate_id=event.get("candidate_id"),
        proposal_id=event.get("proposal_id"),
        run_id=event.get("run_id"),
        wall_time_s=event.get("wall_time_s"),
        error=event.get("error"),
        advance=event.get("advance"),
        blocked_by=event.get("blocked_by"),
    )
