"""The Hub's live attachment/status contract; retained design remains P036's."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from archflow_studio_api.transport.runtime import RuntimeDto

from .models import ChatSummary, HubError


class WorkerStatus(BaseModel):
    workerId: str
    serviceId: str
    projectId: str | None = None
    projectDir: str | None = None
    instanceId: str
    processId: int | None = None
    desiredState: str
    state: Literal["starting", "ready", "busy", "stopping", "stopped", "crashed", "recovering", "unavailable"]
    healthy: bool
    url: str | None = None
    error: HubError | None = None


class OperationRecord(BaseModel):
    operationId: str
    projectId: str
    kind: str
    source: str
    status: Literal["queued", "planning", "validated", "executing", "committing", "completed", "failed", "cancelled", "stale", "needs_recovery"]
    baseRevision: int | None = None
    baseDigest: str | None = None
    baseRecordDigest: str | None = None
    sourceRunId: str | None = None
    sourceStageRef: str | None = None
    proposalId: str | None = None
    jobId: str | None = None
    candidateId: str | None = None
    resultRevision: int | None = None
    resultDigest: str | None = None
    committed: bool = False
    reason: str | None = None
    sessionId: str | None = None
    admissionSequence: int | None = Field(default=None, ge=1, description=(
        "Project-scoped request order projected from the existing Hub admission journal. "
        "Not a completion order or design version; null for observations without a journal entry."
    ))
    createdAt: str | None = Field(default=None, description=(
        "When this Hub admitted the request (ISO 8601, UTC). Null for requests admitted before "
        "admission times were kept and for observations of retained runs."
    ))
    acknowledgedAt: str | None = Field(default=None, description=(
        "When a person dismissed the notice of this failed or stale operation. Reported only while "
        "the operation is failed or stale; it changes no status, reason or result."
    ))


class ProjectRuntimeDto(BaseModel):
    runtimeId: str
    projectId: str
    projectDir: str
    state: Literal["open", "closed"] = "open"
    workers: list[WorkerStatus] = Field(default_factory=list)
    operations: list[OperationRecord] = Field(default_factory=list)
    sessions: list[ChatSummary] = Field(default_factory=list)
    retained: RuntimeDto | None = None
    projection: Literal["unknown", "ready", "stale", "rebuilding"] = "unknown"
    clients: int = 0
    error: HubError | None = None


class HubRuntimeDto(BaseModel):
    serverId: str
    sequence: int
    projects: list[ProjectRuntimeDto]
    workers: list[WorkerStatus]


class RuntimeEvent(BaseModel):
    serverId: str
    sequence: int
    kind: str
    runtimeId: str | None = None
    snapshot: HubRuntimeDto | None = None


class OpenRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectId: str = Field(min_length=1)
    projectDir: str = Field(min_length=1)


class RuntimeProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectId: str = Field(min_length=1)


class OperationAcknowledgeRequest(BaseModel):
    """The runtime and project whose operation notice a person dismissed."""

    model_config = ConfigDict(extra="forbid", strict=True)
    runtimeId: str = Field(min_length=1)
    projectId: str = Field(min_length=1)
