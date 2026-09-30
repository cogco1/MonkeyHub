"""Runtime observations keep process jobs separate from retained project facts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.runtime import RuntimeSnapshot, WorktreeGraph
from .candidate import JobDto, job_dto
from .design_history import DesignBranchDto, DesignStageDto, branch_dto, stage_dto
from .project import ProjectVersionDto
from .working_draft import WorkingHeadDto, working_head_dto


class RuntimeCandidateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    candidate_id: str = Field(alias="candidateId")
    status: str
    job_id: str | None = Field(alias="jobId")
    proposal_id: str | None = Field(alias="proposalId")
    base: ProjectVersionDto | None
    base_record_digest: str | None = Field(alias="baseRecordDigest")
    base_state_digest: str | None = Field(alias="baseStateDigest", description="Exact StateRecord operator base binding digest, as retained by the candidate delta.")
    result_record_digest: str | None = Field(alias="resultRecordDigest")
    result_state_digest: str | None = Field(alias="resultStateDigest")
    receipt_ref: str | None = Field(alias="receiptRef")
    commit_stage_refs: list[str] = Field(alias="commitStageRefs")
    error: str | None


class RuntimeDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    project_dir: str = Field(alias="projectDir")
    published: ProjectVersionDto
    jobs: list[JobDto]
    candidates: list[RuntimeCandidateDto]
    branches: list[DesignBranchDto]
    stages: list[DesignStageDto]
    errors: list[str]
    runs_scanned: int = Field(alias="runsScanned")
    has_more: bool = Field(alias="hasMore")


def runtime_dto(value: RuntimeSnapshot) -> RuntimeDto:
    def version(ref):
        return None if ref is None else ProjectVersionDto(version=ref.version, state_sha256=ref.state_sha256)

    return RuntimeDto(
        project_id=value.project_id, project_dir=value.project_dir, published=version(value.published),
        jobs=[job_dto(job) for job in value.jobs],
        candidates=[RuntimeCandidateDto(
            candidate_id=row.candidate_id, status=row.status, job_id=row.job_id, proposal_id=row.proposal_id,
            base=version(row.base), base_record_digest=row.base_record_digest, base_state_digest=row.base_state_digest,
            result_record_digest=row.result_record_digest, result_state_digest=row.result_state_digest,
            receipt_ref=row.receipt_ref, commit_stage_refs=list(row.commit_stage_refs), error=row.error,
        ) for row in value.candidates],
        branches=[branch_dto(branch) for branch in value.branches], stages=[stage_dto(stage) for stage in value.stages],
        errors=list(value.errors), runs_scanned=value.runs_scanned, has_more=value.has_more,
    )


class WorktreeLineDto(BaseModel):
    """One line of work: the head, another accepted line, running work or a retained result."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    line_id: str = Field(alias="lineId")
    kind: Literal["head", "branch", "running", "result"]
    run_id: str | None = Field(alias="runId")
    job_id: str | None = Field(alias="jobId")
    label: str | None
    base_run_id: str | None = Field(alias="baseRunId", description="The exact retained source this line started from.")
    base_stage_ref: str | None = Field(alias="baseStageRef")
    branch_id: str | None = Field(alias="branchId")
    status: Literal["current", "accepted", "queued", "running", "interrupted", "ready"]
    relation: Literal["head", "ahead", "behind", "diverged", "separate"] = Field(
        description="ahead continues the head; behind started from an older head; diverged shares an older source; separate shares none shown here.")
    reads: list[str]
    writes: list[str] = Field(description="Declared write scope for running work; changed refs since the shared source for results.")
    reconcile: Literal["none", "can-combine", "conflict", "unknown"] = Field(
        description="Whether the StateRecord combine rule accepts this line together with the head; nothing is merged.")
    conflicts: list[str]
    detail: str | None
    updated_at: str | None = Field(alias="updatedAt")
    admission: Literal["admitted", "rejected", "superseded", "none"] = Field(
        description="The line's retained verdict: an admitted Candidate (legacy Stage, Exploration and accepted-episode "
        "facts count), a result the architect turned down, an attempt a closed loop replaced, or none yet. "
        "Running work is always none.")
    study_id: str | None = Field(alias="studyId", description="The Study that verdict grouped the run into, if any.")


class RepresentationStateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    kind: Literal["drawing", "render"]
    item_id: str = Field(alias="itemId")
    label: str
    state: Literal["current", "stale", "frozen", "running", "unavailable"]
    source_run_id: str | None = Field(alias="sourceRunId")
    detail: str | None


class WorktreeGraphDto(BaseModel):
    """A read-only view of the project's current head, active work and other lines."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    head: WorkingHeadDto | None
    revision_sha256: str | None = Field(alias="revisionSha256")
    lines: list[WorktreeLineDto]
    representations: list[RepresentationStateDto]
    warnings: list[str]


def worktree_graph_dto(value: WorktreeGraph) -> WorktreeGraphDto:
    return WorktreeGraphDto(
        project_id=value.project_id, head=working_head_dto(value.head), revision_sha256=value.revision_sha256,
        lines=[WorktreeLineDto(
            line_id=line.line_id, kind=line.kind, run_id=line.run_id, job_id=line.job_id, label=line.label,
            base_run_id=line.base_run_id, base_stage_ref=line.base_stage_ref, branch_id=line.branch_id,
            status=line.status, relation=line.relation, reads=list(line.reads), writes=list(line.writes),
            reconcile=line.reconcile, conflicts=list(line.conflicts), detail=line.detail, updated_at=line.updated_at,
            admission=line.admission, study_id=line.study_id,
        ) for line in value.lines],
        representations=[RepresentationStateDto(
            kind=row.kind, item_id=row.item_id, label=row.label, state=row.state,
            source_run_id=row.source_run_id, detail=row.detail,
        ) for row in value.representations],
        warnings=list(value.warnings),
    )
