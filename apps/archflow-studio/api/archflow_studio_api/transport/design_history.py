"""Design history uses URI references on the wire and existing model sources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.design_portfolio import DesignBranch

from ..application.design_history import DesignHistory, StageView
from .artifacts import ModelSourceDto, model_source_dto


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class DesignStageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    stage_ref: str = Field(alias="stageRef")
    parent_stage_ref: str | None = Field(alias="parentStageRef")
    branch_id: str = Field(alias="branchId")
    label: str
    candidate_id: str = Field(alias="candidateId")
    model_source: ModelSourceDto = Field(alias="modelSource")
    record_digest: str = Field(alias="recordDigest")
    accepted_by: str = Field(alias="acceptedBy")


class DesignBranchDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    branch_id: str = Field(alias="branchId")
    parent_branch: str | None = Field(alias="parentBranch")
    fork_stage_ref: str = Field(alias="forkStageRef")
    head_stage_ref: str = Field(alias="headStageRef")


class DesignHistoryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    branches: list[DesignBranchDto]
    branch_id: str = Field(alias="branchId")
    stages: list[DesignStageDto]


class InitializeDesignStageRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    branch_id: str = Field(alias="branchId", default="main", pattern=IDENTIFIER_PATTERN)
    label: str = Field(default="S0", min_length=1, max_length=240)
    model_source: ModelSourceDto = Field(alias="modelSource")


class AcceptDesignCandidateRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    branch_id: str = Field(alias="branchId", default="main", pattern=IDENTIFIER_PATTERN)
    expected_head_stage_ref: str = Field(alias="expectedHeadStageRef", min_length=1)
    label: str | None = Field(default=None, min_length=1, max_length=240)


class ForkDesignBranchRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    branch_id: str = Field(alias="branchId", pattern=IDENTIFIER_PATTERN)
    parent_branch: str = Field(alias="parentBranch", pattern=IDENTIFIER_PATTERN)
    stage_ref: str = Field(alias="stageRef", min_length=1)


def stage_dto(view: StageView) -> DesignStageDto:
    return DesignStageDto(
        stage_ref=view.ref.uri,
        parent_stage_ref=None if view.stage.parent_stage is None else view.stage.parent_stage.uri,
        branch_id=view.stage.branch_id, label=view.stage.label,
        candidate_id=view.stage.candidate_id, model_source=model_source_dto(view.model_source),
        record_digest=view.record_digest, accepted_by=view.stage.accepted_by,
    )


def branch_dto(branch: DesignBranch) -> DesignBranchDto:
    return DesignBranchDto(branch_id=branch.branch_id, parent_branch=branch.parent_branch,
                           fork_stage_ref=branch.fork_stage.uri, head_stage_ref=branch.head_stage.uri)


def history_dto(history: DesignHistory) -> DesignHistoryDto:
    return DesignHistoryDto(project_id=history.project_id, branch_id=history.branch_id,
                            branches=[branch_dto(branch) for branch in history.branches],
                            stages=[stage_dto(stage) for stage in history.stages])
