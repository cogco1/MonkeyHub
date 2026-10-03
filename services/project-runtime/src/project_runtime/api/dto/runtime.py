"""Runtime observations keep process jobs separate from retained project facts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...application.retention import TrashView
from ...status import LineStep, RuntimeSnapshot, WorktreeGraph
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
        # Each run keeps its first wire names (candidates[], candidateId): the
        # Hub's recovery and the web client read them.
        candidates=[RuntimeCandidateDto(
            candidate_id=row.run_id, status=row.status, job_id=row.job_id, proposal_id=row.proposal_id,
            base=version(row.base), base_record_digest=row.base_record_digest, base_state_digest=row.base_state_digest,
            result_record_digest=row.result_record_digest, result_state_digest=row.result_state_digest,
            receipt_ref=row.receipt_ref, commit_stage_refs=list(row.commit_stage_refs), error=row.error,
        ) for row in value.runs],
        branches=[branch_dto(branch) for branch in value.branches], stages=[stage_dto(stage) for stage in value.stages],
        errors=list(value.errors), runs_scanned=value.runs_scanned, has_more=value.has_more,
    )


class WorktreeLineDto(BaseModel):
    """One line of work: the head, another accepted line, running work or a retained result, superseded or not."""

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
    relation: Literal["head", "ahead", "behind", "diverged", "superseded", "separate"] = Field(
        description="ahead continues the head; behind started from an older head; diverged shares an older source; "
        "superseded is a diverged draft built where the head's line later moved on through another step (supersededBy) "
        "and changing what the line changed since (reconcile conflict), which nobody continued or admitted, nor anything "
        "built on it (#575); separate shares none shown here.")
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
    superseded_by: str | None = Field(alias="supersededBy", description=
        "For a superseded draft: the step of the head's line that replaced it, the one made from where the draft started. "
        "Null for every other line.")


class LineStepDto(BaseModel):
    """One run of the Working Head's line (#575), in the words its retained facts give it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    run_id: str = Field(alias="runId")
    base_run_id: str | None = Field(alias="baseRunId", description=
        "The run this step was made from; null for a run made from no other. The line's first step names its own base "
        "only when the line was cut short.")
    label: str | None = Field(description=
        "The name it was given: a saved version's label, else its accepted Stage's, else its admitted result's.")
    request: str | None = Field(description=
        "The words that asked for it, when retained: its admission's rawLanguage, else the request its own run keeps "
        "(the sentence or outside agent's summary its proposal was made from), else the sentence an intent model "
        "compiled into it. Null for a change nobody asked for in words.")
    summary: str | None = Field(description="An admitted result's own summary of the change.")
    stage_ref: str | None = Field(alias="stageRef", description="The accepted Stage this run is, if it is one.")
    updated_at: str | None = Field(alias="updatedAt", description="When the working position last listed or moved onto it.")


class RepresentationStateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    kind: Literal["drawing", "render"]
    item_id: str = Field(alias="itemId")
    label: str
    state: Literal["current", "stale", "frozen", "running", "unavailable"]
    source_run_id: str | None = Field(alias="sourceRunId")
    detail: str | None


class ActorHeadDto(BaseModel):
    """A person's retained line; displayName is presentation, actorId is identity."""
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    actor_id: str = Field(alias="actorId")
    display_name: str = Field(alias="displayName")
    run_id: str = Field(alias="runId")
    label: str | None = None


class WorktreeGraphDto(BaseModel):
    """A read-only view of the project's current head, active work and other lines."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    head: WorkingHeadDto | None
    revision_sha256: str | None = Field(alias="revisionSha256")
    line: list[LineStepDto] = Field(description=
        "The Working Head's line, oldest first and ending at the head: its first-parent chain, each step with its "
        "retained label, request and summary (#575). Empty without a head.")
    later: list[LineStepDto] = Field(description=
        "After a return to an earlier step: the steps the head moved back past, oldest first, from the one made from "
        "the head to the last one it stood on, along the branch it moved onto most recently where the line forked "
        "(#575). Each can be continued again. Empty while no run the head once stood on continues it.")
    actor_heads: list[ActorHeadDto] = Field(default_factory=list, alias="actorHeads")
    lines: list[WorktreeLineDto]
    representations: list[RepresentationStateDto]
    warnings: list[str]


def _step_dto(step: LineStep) -> LineStepDto:
    return LineStepDto(
        run_id=step.run_id, base_run_id=step.base_run_id, label=step.label, request=step.request,
        summary=step.summary, stage_ref=step.stage_ref, updated_at=step.updated_at,
    )


def worktree_graph_dto(value: WorktreeGraph) -> WorktreeGraphDto:
    return WorktreeGraphDto(
        project_id=value.project_id, head=working_head_dto(value.head), revision_sha256=value.revision_sha256,
        line=[_step_dto(step) for step in value.line],
        later=[_step_dto(step) for step in value.later],
        actor_heads=[ActorHeadDto(actor_id=head.actor_id, display_name=head.display_name,
                                 run_id=head.run_id, label=head.label) for head in value.actor_heads],
        lines=[WorktreeLineDto(
            line_id=line.line_id, kind=line.kind, run_id=line.run_id, job_id=line.job_id, label=line.label,
            base_run_id=line.base_run_id, base_stage_ref=line.base_stage_ref, branch_id=line.branch_id,
            status=line.status, relation=line.relation, reads=list(line.reads), writes=list(line.writes),
            reconcile=line.reconcile, conflicts=list(line.conflicts), detail=line.detail, updated_at=line.updated_at,
            admission=line.admission, study_id=line.study_id, superseded_by=line.superseded_by,
        ) for line in value.lines],
        representations=[RepresentationStateDto(
            kind=row.kind, item_id=row.item_id, label=row.label, state=row.state,
            source_run_id=row.source_run_id, detail=row.detail,
        ) for row in value.representations],
        warnings=list(value.warnings),
    )


# ---- the project trash (#575) ------------------------------------------------------------------------


class TrashEntryDto(BaseModel):
    """One run in the project trash: what moved, why, what superseded it, and until when it can be restored."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    run_id: str = Field(alias="runId")
    trashed_at: str = Field(alias="trashedAt", description="When it moved into the trash.")
    expires_at: str = Field(alias="expiresAt", description="When it is purged; until then it can be restored.")
    rule: str = Field(description=
        "Which retention rule moved it: superseded (a draft the head's line superseded), replaced-attempt (an attempt "
        "an admitted result replaced, or one its loop withdrew) or failed-attempt (a design change whose run never finished).")
    reason: str = Field(description="Why it was moved, in one sentence.")
    superseded_by: str | None = Field(alias="supersededBy", description=
        "The run that superseded it: the line's step for a superseded draft, the admitted result for a replaced attempt.")
    base_run_id: str | None = Field(alias="baseRunId", description="The run it was made from.")
    label: str | None = Field(description="Its name or the words that asked for it, when it had them.")
    state_digest: str | None = Field(alias="stateDigest", description="Its design state's digest, when it finished.")


class ProjectTrashDto(BaseModel):
    """The project trash, oldest entry first."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    retention_days: int = Field(alias="retentionDays", description="How long an entry can be restored before it is purged.")
    entries: list[TrashEntryDto]


class TrashRestoreRequestDto(BaseModel):
    """Restore one trashed run: it comes back whole, with any trashed run it was made from."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class TrashRestoreDto(BaseModel):
    """What came back, and the trash as it now is."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    restored: list[str] = Field(description="The runs that came back: the one asked for first, then those it names.")
    trash: ProjectTrashDto


def trash_dto(value: TrashView) -> ProjectTrashDto:
    return ProjectTrashDto(
        project_id=value.project_id, retention_days=value.retention_days,
        entries=[TrashEntryDto(
            run_id=entry.run_id, trashed_at=entry.trashed_at, expires_at=value.expires_at(entry), rule=entry.rule,
            reason=entry.reason, superseded_by=entry.superseded_by, base_run_id=entry.base_run_id, label=entry.label,
            state_digest=entry.state_digest,
        ) for entry in value.entries],
    )
