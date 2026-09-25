"""Design history uses URI references on the wire and existing model sources."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.design_portfolio import DesignBranch

from ..application.design_history import (
    AcceptanceEvidence,
    AdmissionRecord,
    AdmittedBy,
    CandidatePool,
    DesignHistory,
    PoolCandidate,
    PoolStudy,
    StageView,
)
from .artifacts import ModelSourceDto, model_source_dto
from .decisions import MessageSourceDto


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
RunId = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]


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


class AdmissionActorDto(BaseModel):
    """Who closed a loop and through which surface, as the boundary resolved it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    actor_id: str = Field(alias="actorId")
    authenticated: bool | None = Field(description="Null only where a legacy fact does not say.")
    origin: str | None = Field(
        description="studio or hub for a person's act, hub-agent for the Hub Agent, retroactive for a "
        "one-time review; null only where a legacy fact does not say.")


class DesignCandidateDto(BaseModel):
    """One admitted Candidate of the project's pool (#294)."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    candidate_id: str = Field(alias="candidateId", description="The result run.")
    outcome: Literal["admitted", "rejected"] = Field(
        description="Always admitted unless include=rejected also asked for retained rejections.")
    label: str | None
    summary: str | None
    base_stage_ref: str | None = Field(
        alias="baseStageRef",
        description="The Stage it was built from (its retained change's source Stage); null under the Unstaged root.")
    study_id: str | None = Field(alias="studyId", description="The Study it belongs to; null when ungrouped.")
    model_source: ModelSourceDto | None = Field(
        alias="modelSource", description="The exact complete model it was admitted with; anchors previews.")
    admitted_by: AdmissionActorDto | None = Field(alias="admittedBy")
    admitted_at: str | None = Field(alias="admittedAt")
    admission_ref: str | None = Field(
        alias="admissionRef",
        description="The retained fact that admits it: its CandidateAdmission@1, or for legacy the Stage, "
        "Exploration or episode record.")
    legacy: Literal["stage", "working-copy", "episode"] | None = Field(
        description="Which earlier fact admits it when no admission record does; null for a record.")
    accepted_stage_ref: str | None = Field(
        alias="acceptedStageRef",
        description="The Stage it became, or the Stage whose accepted run it is the nearest admitted ancestor of.")
    continued_from: str | None = Field(
        alias="continuedFrom", description="Its nearest admitted ancestor Candidate, from retained lineage.")
    in_working_head_lineage: bool = Field(alias="inWorkingHeadLineage")
    blocked_by: list[str] = Field(
        alias="blockedBy",
        description="The review-readiness clauses that did not hold when a person admitted it as a comparison "
        "option (the violation marker, owner decision Q2); empty when it was review-ready.")
    supersedes: list[str] = Field(description="Attempts this result replaced within its loop; hidden by default.")


class DesignStudyDto(BaseModel):
    """One Study: a declared group, one closed loop's own group, or a retained Exploration."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: str
    label: str | None
    base_run_id: str | None = Field(alias="baseRunId")
    base_stage_ref: str | None = Field(alias="baseStageRef")
    candidate_ids: list[str] = Field(alias="candidateIds")
    source: Literal["declared", "admission", "working-copy"]


class DesignHistoryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    branches: list[DesignBranchDto]
    branch_id: str = Field(alias="branchId")
    stages: list[DesignStageDto]
    candidates: list[DesignCandidateDto] = Field(
        description="The project's admitted Candidates, from admission records and legacy facts; never every run.")
    studies: list[DesignStudyDto]
    warnings: list[str] = Field(description="Admission facts that could not be read or compete; those runs are left out.")


class AdmissionTaskDto(BaseModel):
    """Which closed loop a verdict belongs to, as the caller states it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    kind: Literal["ui", "hub-chat", "retroactive"] = Field(
        description="ui: a person's act; hub-chat: the Hub Agent closing a chat task on the user's behalf, which must "
        "be review-ready; retroactive: a one-time review a person confirms.")
    ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=16, description="Opaque task ids, such as Hub operation or turn ids.")


class AdmissionStudyRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    id: str = Field(pattern=IDENTIFIER_PATTERN, description="The Study's id; later loops of the same Study repeat it.")
    label: str = Field(min_length=1, max_length=240)
    base_run_id: str = Field(alias="baseRunId", pattern=IDENTIFIER_PATTERN,
                             description="The exact run every result of the Study is built from.")


class AdmissionResultRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", pattern=IDENTIFIER_PATTERN)
    outcome: Literal["admitted", "rejected"]
    supersedes: list[RunId] = Field(
        default_factory=list, max_length=64,
        description="Attempt runs this result replaced within the loop; they leave the tree and stay readable.")
    label: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)


class AdmissionRequestDto(BaseModel):
    """One closed loop's verdict: each result it produced, admitted or rejected."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    task: AdmissionTaskDto
    study: AdmissionStudyRequestDto | None = None
    message_source: MessageSourceDto | None = Field(
        alias="messageSource", default=None,
        description="The chat message this loop answers, as the Hub binds it; provenance, never a credential.")
    raw_language: str | None = Field(
        alias="rawLanguage", default=None, min_length=1, max_length=2000,
        description="The user's own words that carry the decision; required for an Agent's rejection.")
    results: list[AdmissionResultRequestDto] = Field(min_length=1, max_length=64)


class AdmissionStudyDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: str
    label: str | None
    base_run_id: str | None = Field(alias="baseRunId")
    base_stage_ref: str | None = Field(alias="baseStageRef")


class AdmissionResultDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    run_id: str = Field(alias="runId")
    outcome: Literal["admitted", "rejected"]
    model_source: ModelSourceDto | None = Field(alias="modelSource", description="Pinned for an admitted result.")
    receipt_ref: str = Field(alias="receiptRef")
    record_digest: str = Field(alias="recordDigest")
    base_stage_ref: str | None = Field(alias="baseStageRef")
    supersedes: list[str]
    label: str | None
    summary: str | None
    reason: str | None
    blocked_by: list[str] | None = Field(
        alias="blockedBy", description="Review-readiness clauses that did not hold; null for a rejection.")


class CandidateAdmissionDto(BaseModel):
    """One retained CandidateAdmission@1: a closed loop's verdict, facts only."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    admission_id: str = Field(alias="admissionId")
    admission_ref: str = Field(alias="admissionRef")
    project_id: str = Field(alias="projectId")
    previous_revision_ref: str | None = Field(alias="previousRevisionRef")
    occurred_at: str = Field(alias="occurredAt")
    actor: AdmissionActorDto
    message_source: MessageSourceDto | None = Field(alias="messageSource")
    raw_language: str | None = Field(alias="rawLanguage")
    task: AdmissionTaskDto
    study: AdmissionStudyDto | None
    results: list[AdmissionResultDto]


class AdmissionListDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    admissions: list[CandidateAdmissionDto]
    warnings: list[str]


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


def _actor_dto(actor: AdmittedBy | None) -> AdmissionActorDto | None:
    if actor is None:
        return None
    return AdmissionActorDto(actor_id=actor.actor_id, authenticated=actor.authenticated, origin=actor.origin)


def candidate_dto(candidate: PoolCandidate) -> DesignCandidateDto:
    return DesignCandidateDto(
        candidate_id=candidate.candidate_id, outcome=candidate.outcome, label=candidate.label,
        summary=candidate.summary, base_stage_ref=candidate.base_stage_ref, study_id=candidate.study_id,
        model_source=model_source_dto(candidate.model_source), admitted_by=_actor_dto(candidate.admitted_by),
        admitted_at=candidate.admitted_at, admission_ref=candidate.admission_ref, legacy=candidate.legacy,
        accepted_stage_ref=candidate.accepted_stage_ref, continued_from=candidate.continued_from,
        in_working_head_lineage=candidate.in_working_head_lineage, blocked_by=list(candidate.blocked_by),
        supersedes=list(candidate.supersedes),
    )


def study_dto(study: PoolStudy) -> DesignStudyDto:
    return DesignStudyDto(id=study.study_id, label=study.label, base_run_id=study.base_run_id,
                          base_stage_ref=study.base_stage_ref, candidate_ids=list(study.candidate_ids),
                          source=study.source)


def history_dto(history: DesignHistory) -> DesignHistoryDto:
    pool: CandidatePool = history.pool
    return DesignHistoryDto(project_id=history.project_id, branch_id=history.branch_id,
                            branches=[branch_dto(branch) for branch in history.branches],
                            stages=[stage_dto(stage) for stage in history.stages],
                            candidates=[candidate_dto(candidate) for candidate in pool.candidates],
                            studies=[study_dto(study) for study in pool.studies],
                            warnings=list(pool.warnings))


def admission_dto(record: AdmissionRecord) -> CandidateAdmissionDto:
    """One retained record as the wire carries it; nothing is recomputed."""

    payload = record.payload
    actor, study = payload["actor"], payload.get("study")
    return CandidateAdmissionDto(
        admission_id=payload["admissionId"], admission_ref=record.ref, project_id=payload["projectId"],
        previous_revision_ref=payload.get("previousRevisionRef"), occurred_at=payload["occurredAt"],
        actor=AdmissionActorDto(actor_id=actor["actorId"], authenticated=actor.get("authenticated"),
                                origin=actor.get("origin")),
        message_source=payload.get("messageSource"), raw_language=payload.get("rawLanguage"),
        task=AdmissionTaskDto.model_validate(payload["task"]),
        study=None if study is None else AdmissionStudyDto(
            id=study["id"], label=study.get("label"), base_run_id=study.get("baseRunId"),
            base_stage_ref=study.get("baseStageRef")),
        results=[AdmissionResultDto(
            run_id=row["runId"], outcome=row["outcome"], model_source=row.get("modelSource"),
            receipt_ref=row["receiptRef"], record_digest=row["recordDigest"], base_stage_ref=row.get("baseStageRef"),
            supersedes=list(row["supersedes"]), label=row.get("label"), summary=row.get("summary"),
            reason=row.get("reason"), blocked_by=row.get("blockedBy"),
        ) for row in payload["results"]],
    )
