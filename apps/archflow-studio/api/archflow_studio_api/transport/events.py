"""The wire form of one progress event.

``seq`` is the whole contract with a client that reconnects: it is the id on
the SSE frame, and the number the browser sends back as ``Last-Event-ID``. The
rest is a candidate's identity restated on every event, because a stream is not
a conversation — a client that joined late, or resumed, must be able to read
one frame and know which job and which run it is about without having seen the
frames before it.

Asset registration and work-item additions identify the updated source run;
clients read the current lists before presenting a newly available model.
Fields a given event has nothing to say about travel as ``null`` rather than
being omitted, so the shape a client parses stays the same.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ModelLoadTimingDto(BaseModel):
    """Client elapsed time from artifact download to viewport load completion."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    event_id: UUID = Field(alias="eventId")
    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    source_ref: str | None = Field(alias="sourceRef", default=None)
    started_at: AwareDatetime = Field(alias="startedAt")
    ended_at: AwareDatetime = Field(alias="endedAt")
    duration_ms: int = Field(alias="durationMs", ge=0)
    status: Literal["succeeded", "failed", "cancelled"]

    @model_validator(mode="after")
    def ordered_interval(self) -> ModelLoadTimingDto:
        if self.ended_at < self.started_at:
            raise ValueError("endedAt precedes startedAt")
        return self


class MonitorWriteDto(BaseModel):
    """A diagnostic acknowledgement; it does not change a model or project."""

    recorded: bool


class ClientTimingDetailsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    active_wait_ms: int | None = Field(default=None, ge=0)
    between_actions_ms: int | None = Field(default=None, ge=0)
    input_bytes: int | None = Field(default=None, ge=0)
    asset_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retry_attempt: int | None = Field(default=None, ge=0)
    blocking: bool | None = None
    request_kind: Literal["candidate_poll", "candidate_read", "artifact_bytes", "document_bytes"] | None = None


class ClientTimingDto(BaseModel):
    """Browser intervals for one visible action; no message or document content."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    event_id: UUID = Field(alias="eventId")
    operation_id: UUID = Field(alias="operationId")
    parent_event_id: UUID | None = Field(alias="parentEventId", default=None)
    phase: Literal["design_edit", "intent_wait", "candidate_wait", "model_load", "model_download", "model_parse", "model_install", "model_projection", "drawing_wait", "document_load", "document_render", "stage_wait", "api_wait"]
    project_id: str = Field(alias="projectId", min_length=1)
    run_id: str | None = Field(alias="runId", default=None, min_length=1)
    source_ref: str | None = Field(alias="sourceRef", default=None)
    started_at: AwareDatetime = Field(alias="startedAt")
    ended_at: AwareDatetime | None = Field(alias="endedAt", default=None)
    duration_ms: int | None = Field(alias="durationMs", default=None, ge=0)
    status: Literal["running", "succeeded", "failed", "cancelled"]
    details: ClientTimingDetailsDto = Field(default_factory=ClientTimingDetailsDto)

    @model_validator(mode="after")
    def ordered_interval(self) -> ClientTimingDto:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("endedAt precedes startedAt")
        if self.status != "running" and (self.ended_at is None or self.duration_ms is None):
            raise ValueError("completed timing requires endedAt and durationMs")
        return self


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
        "candidate.succeeded | candidate.failed | validation.computed | "
        "model_asset.registered | working_copy.option_added",
    )
    job_id: str | None = Field(alias="jobId", default=None)
    candidate_id: str | None = Field(alias="candidateId", default=None)
    proposal_id: str | None = Field(alias="proposalId", default=None)
    run_id: str | None = Field(alias="runId", default=None)
    wall_time_s: float | None = Field(alias="wallTimeS", default=None)
    error: str | None = Field(default=None)
    review_ready: bool | None = Field(
        alias="reviewReady",
        default=None,
        description="on validation.computed, whether the candidate is ready "
        "for human review; null on lifecycle events. This is not issue authority",
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
        review_ready=event.get("review_ready"),
        blocked_by=event.get("blocked_by"),
    )
