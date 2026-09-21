"""Current working position, explicit saves and unexecuted local recovery."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkingDraftEntryDto(BaseModel):
    runId: str
    sourceStageRef: str | None = None
    branchId: str | None = None
    updatedAt: str
    label: str | None = None


class LocalDraftSourceDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str
    stateDigest: str
    sourceRunId: str | None = None
    sourceStageRef: str | None = None


class LocalDraftInputDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: LocalDraftSourceDto
    commands: list[dict[str, Any]] = Field(max_length=10000)
    # The client retains both its synced prefix and the frozen pending request.
    # This is recovery data only; the server never executes these mappings.
    attempt: dict[str, Any] | None = None


class LocalDraftDto(LocalDraftInputDto):
    updatedAt: str


class WorkingDraftDto(BaseModel):
    projectId: str
    revisionSha256: str | None = None
    current: WorkingDraftEntryDto | None = None
    recovery: list[WorkingDraftEntryDto] = Field(default_factory=list)
    saved: list[WorkingDraftEntryDto] = Field(default_factory=list)
    managedRunIds: list[str] = Field(default_factory=list)
    localDraft: LocalDraftDto | None = None


class WorkingDraftSelectionDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    runId: str | None
    branchId: str | None = None


class WorkingDraftSaveDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    runId: str = Field(min_length=1)
    label: str | None = Field(default=None, max_length=200)


class LocalDraftRequestDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str
    baseRevisionSha256: str | None
    draft: LocalDraftInputDto | None
    expectedSource: LocalDraftSourceDto | None = None
