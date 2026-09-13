"""Runtime observations keep process jobs separate from retained project facts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.runtime import RuntimeSnapshot
from .candidate import JobDto, job_dto
from .design_history import DesignBranchDto, DesignStageDto, branch_dto, stage_dto
from .project import ProjectVersionDto


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
