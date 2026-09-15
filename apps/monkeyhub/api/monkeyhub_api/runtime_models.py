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


class ArtifactUpdatedEvent(BaseModel):
    """One objective update from an explicitly bound project work artifact."""

    projectId: str
    sourceId: str
    artifactId: str
    label: str
    state: Literal["working", "candidate", "accepted"]
    previousSha256: str
    sha256: str
    size: int = Field(ge=0)
    mtimeNs: int = Field(ge=0)


class RuntimeEvent(BaseModel):
    serverId: str
    sequence: int
    kind: str
    runtimeId: str | None = None
    snapshot: HubRuntimeDto | None = None
    artifact: ArtifactUpdatedEvent | None = None


class OpenRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectId: str = Field(min_length=1)
    projectDir: str = Field(min_length=1)


class RuntimeProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectId: str = Field(min_length=1)
