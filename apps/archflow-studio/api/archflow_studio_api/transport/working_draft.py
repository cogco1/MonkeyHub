"""Current working position, explicit saves and unexecuted local recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ModelSourceDto, model_source_dto

if TYPE_CHECKING:
    from ..application.working_draft import WorkingSource


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


class WorkingHeadDto(BaseModel):
    """The project's current valid working state; ordinary work follows it."""

    runId: str
    stateDigest: str
    recordDigest: str
    sourceStageRef: str | None = Field(default=None, description="The accepted Stage this state is, or continues from.")
    branchId: str | None = None
    accepted: bool = Field(description="True only when the head run is exactly the accepted Stage's model run.")
    origin: Literal["working-position", "branch-head", "reference"] = Field(
        description="Which retained fact answered: the saved working position, the main line's accepted head, or the reference run.")
    label: str | None = None
    modelSource: ModelSourceDto | None = None
    lineage: list[str] = Field(description="The head run first, then each exact retained source it continued.")


class WorkingRevisionDto(BaseModel):
    """Only the working position's revision: a cheap check for whether the head may have moved."""

    projectId: str
    revisionSha256: str | None = None


class WorkingSourceDto(BaseModel):
    """One workspace's current source under a LIVE or FROZEN policy."""

    projectId: str
    workspace: Literal["modeling", "drawing", "render", "board"]
    policy: Literal["live", "frozen"]
    revisionSha256: str | None = Field(default=None, description="The working position revision; it changes whenever the head moves.")
    head: WorkingHeadDto | None = None
    compatible: bool
    source: ModelSourceDto | None = Field(default=None, description="The exact model this workspace should use.")
    stageRef: str | None = Field(default=None, description="Set only when source is exactly an accepted Stage's pinned model.")
    reason: str | None = Field(default=None, description="Why the workspace cannot follow the head, or why a frozen pin is no longer current.")
    warnings: list[str] = Field(default_factory=list)


def working_head_dto(head) -> WorkingHeadDto | None:
    return None if head is None else WorkingHeadDto(
        runId=head.run_id, stateDigest=head.state_digest, recordDigest=head.record_digest,
        sourceStageRef=head.source_stage_ref, branchId=head.branch_id, accepted=head.accepted, origin=head.origin,
        label=head.label, modelSource=model_source_dto(head.model_source), lineage=list(head.lineage))


def working_source_dto(value: WorkingSource) -> WorkingSourceDto:
    return WorkingSourceDto(
        projectId=value.project_id, workspace=value.workspace, policy=value.policy, revisionSha256=value.revision_sha256,
        head=working_head_dto(value.head), compatible=value.compatible, source=model_source_dto(value.source),
        stageRef=value.stage_ref, reason=value.reason, warnings=list(value.warnings))
