"""Design history uses URI references on the wire and existing model sources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.design_portfolio import DesignBranch

from ..application.design_history import AcceptanceEvidence, DesignHistory, StageView
from .artifacts import ModelSourceDto, model_source_dto


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class AcceptanceEvidenceDto(BaseModel):
    """The retained acceptance evidence of one Stage, or absent where none is.

    ``acceptedBy`` remains the actor id on the public wire. New retained Stages
    may use an opaque internal acceptance principal so the exact winning origin
    can be recovered after a crash; that principal is never exposed here.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    event_id: str = Field(alias="eventId")
    occurred_at: str = Field(alias="occurredAt")
    action: str
    status: str
    actor_id: str = Field(alias="actorId")
    authenticated: bool
    origin: str
    audit_ref: str = Field(alias="auditRef")


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
    # Null for a Stage committed before acceptance evidence was retained: the
    # older record stays readable and says nothing it cannot prove.
    acceptance: AcceptanceEvidenceDto | None = Field(default=None)


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


def acceptance_dto(evidence: AcceptanceEvidence | None) -> AcceptanceEvidenceDto | None:
    if evidence is None:
        return None
    return AcceptanceEvidenceDto(
        event_id=evidence.event_id, occurred_at=evidence.occurred_at,
        action=evidence.action, status=evidence.status, actor_id=evidence.actor_id,
        authenticated=evidence.authenticated, origin=evidence.origin,
        audit_ref=evidence.audit_ref.uri,
    )


def stage_dto(view: StageView) -> DesignStageDto:
    accepted_by = (
        view.acceptance_attribution.actor_id
        if view.acceptance_attribution is not None
        else view.stage.accepted_by
    )
    return DesignStageDto(
        stage_ref=view.ref.uri,
        parent_stage_ref=None if view.stage.parent_stage is None else view.stage.parent_stage.uri,
        branch_id=view.stage.branch_id, label=view.stage.label,
        candidate_id=view.stage.candidate_id, model_source=model_source_dto(view.model_source),
        record_digest=view.record_digest, accepted_by=accepted_by,
        acceptance=acceptance_dto(view.acceptance),
    )


def branch_dto(branch: DesignBranch) -> DesignBranchDto:
    return DesignBranchDto(branch_id=branch.branch_id, parent_branch=branch.parent_branch,
                           fork_stage_ref=branch.fork_stage.uri, head_stage_ref=branch.head_stage.uri)


def history_dto(history: DesignHistory) -> DesignHistoryDto:
    return DesignHistoryDto(project_id=history.project_id, branch_id=history.branch_id,
                            branches=[branch_dto(branch) for branch in history.branches],
                            stages=[stage_dto(stage) for stage in history.stages])
