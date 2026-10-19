"""``/api/episodes``: the judgements this process has made, and where they live.

Read-only, and deliberately narrow. A judgement is *written* by deciding — a
rejection or a modification through ``POST /api/proposals/{id}/decision``, an
acceptance by running the proposal as a candidate — and never by asking for one
here. What this route adds is the ability to see a judgement that has not met a
run yet, which is the whole reason the in-memory half exists.

Every episode says where it lives in its own ``persistence`` field: ``run:<id>``
once it is in a run's records, and the in-memory sentence while it is only this
process's. A client that shows the second has been told the truth about what it
is looking at, exactly as it is for a proposal.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ValidationError
from starlette.requests import Request

from ..dto.proposal import EpisodeDto, episode_dto
from ...binding import bound_project
from ...authentication import ActorAttribution, request_attribution
from ...synchronization import accept_shared_candidate, fork_shared_branch
from ...errors import StudioError
from archflow.project.repository import ProjectRepositoryError
from ...monitoring import candidate_event_id
from ...application.design_history import (
    ADMITTED, TASK_HUB_CHAT, accept_design_candidate, admit_results, fork_design_branch, initialize_design_stage,
    list_admissions, read_design_history, save_review_judgement, stage_ref_from,
)
from ...application.decisions import DecisionContext, compile_scoped_decisions
from ...application.projection import project_state, require_actionable
from ...application.compare import compare_runs, shapes_of
from ...application.revision_transaction import Decision, RevisionRequest, revise, revision_context
from archflow.state.state_record import check_obligations, obligation_measured
from ...application.working_draft import resolve_working_source, select_working_draft
from ...application.episodes import (
    add_working_copy_option, list_working_copies,
    read_working_copy, select_working_copy_option,
)
from ..dto.artifacts import model_source_from
from ..dto.construction import ConstructionRequestDto, FacetsRequestDto, HostedOpeningRequestDto
from ..dto.design_history import (
    AcceptDesignCandidateRequestDto, AdmissionListDto, AdmissionRequestDto, CandidateAdmissionDto,
    DesignBranchDto, DesignHistoryDto, DesignStageDto, ReviewJudgementDto, ReviewJudgementRequestDto,
    ForkDesignBranchRequestDto, InitializeDesignStageRequestDto, RevisionOutcomeDto, RevisionRequestDto,
    admission_dto, branch_dto, history_dto, review_dto, revision_dto, stage_dto,
)
from ..dto.proposal import ElevationEditRequestDto, ProposalRequestDto
from ..dto.proposal import (
    WorkingCopyDto, WorkingCopyListDto,
    WorkingCopySelectionRequestDto, WorkingCopyOptionRequestDto,
    working_copy_dto, working_option_from,
)
from .candidates import compare_candidate, read_candidate, read_job, start_candidate
from .construction import create_construction_proposal, create_facets_proposal, create_hosted_opening_proposal
from .proposals import _require_bound_project, create_elevation_proposal, create_proposal
from .validation import read_validation

_INCLUDE = Query(default=None, description="rejected also lists retained rejections and the Agent's withdrawals, for advanced views.")

router = APIRouter(tags=["episodes"])


def _attribution(request: Request) -> ActorAttribution:
    """Who is accepting, from the request boundary rather than from its body."""

    return request_attribution(request)


@router.get("/design-history", response_model=DesignHistoryDto, response_model_by_alias=True)
def read_committed_design_history(request: Request, branch_id: str = Query(default="main", alias="branchId"),
                                  include: Literal["rejected"] | None = _INCLUDE) -> DesignHistoryDto:
    """Committed Stages of one line, and the project's admitted Candidates and Studies."""
    return history_dto(read_design_history(bound_project(request.app.state), branch_id,
                                           include_rejected=include == "rejected", indexed=True))


@router.post("/candidate-reviews", response_model=ReviewJudgementDto, response_model_by_alias=True, status_code=201)
def review_candidate_or_stage(request: Request, payload: ReviewJudgementRequestDto) -> ReviewJudgementDto:
    """Retain a review judgement; it deliberately performs no design-position write."""
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    if request.app.state.settings.sync_url:
        raise StudioError(409, "SYNC_REVIEW_SHARED", "Candidate reviews are not synchronized yet.")
    return review_dto(save_review_judgement(binding, subject_kind=payload.subject_kind,
                                             subject_ref=payload.subject_ref, action=payload.action,
                                             reason=payload.reason, attribution=_attribution(request)))


@router.post("/design-stages/initialize", response_model=DesignStageDto, response_model_by_alias=True, status_code=201)
def initialize_committed_design(request: Request, payload: InitializeDesignStageRequestDto) -> DesignStageDto:
    if request.app.state.settings.sync_url:
        raise StudioError(409, "SYNC_INITIALIZE_SHARED", "Initialize the shared project, then pull its starting Stage into this Runtime.")
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    source = model_source_from(payload.model_source)
    with request.app.state.monitor.measure(
        "stage_save", project_id=binding.project_id, run_id=source.run_id,
    ) as operation:
        saved = initialize_design_stage(binding, model_source=source, branch_id=payload.branch_id, label=payload.label,
                                        accepted_by=_attribution(request).actor_id)
        operation["source_ref"] = saved.stage.model_ref.uri
    return stage_dto(saved)


@router.post("/candidates/{candidate_id}/accept", response_model=DesignStageDto, response_model_by_alias=True)
def accept_committed_design(request: Request, candidate_id: str, payload: AcceptDesignCandidateRequestDto) -> DesignStageDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    if request.app.state.settings.sync_url:
        return DesignStageDto.model_validate(accept_shared_candidate(request.app.state, candidate_id, payload.model_dump(by_alias=True)))
    expected_head = stage_ref_from(binding, payload.expected_head_stage_ref)
    with request.app.state.monitor.measure(
        "stage_save", project_id=binding.project_id, run_id=candidate_id, source_ref=expected_head.uri,
        related_event_id=candidate_event_id(binding.project_id, candidate_id),
    ):
        try:
            saved = accept_design_candidate(binding, candidate_id=candidate_id, branch_id=payload.branch_id,
                                            expected_head=expected_head, events=request.app.state.events, label=payload.label,
                                            attribution=_attribution(request))
        except StudioError as exc:
            # A repository I/O failure before the branch CAS remains an
            # infrastructure interruption, not a committed design failure.
            # ``accept_design_candidate`` first reconciles the branch and only
            # emits DESIGN_BRANCH_COMMIT_FAILED when no reachable winner exists.
            # Preserve the older boundary contract so Hub recovery can mark the
            # operation ``needs_recovery`` instead of treating a prepared Stage
            # as committed. A lost reply *after* CAS never reaches this branch:
            # it is recovered from reachable history and returns normally.
            if exc.code == "DESIGN_BRANCH_COMMIT_FAILED" and isinstance(exc.__cause__, OSError):
                raise exc.__cause__
            raise
    return stage_dto(saved)


@router.get("/admissions", response_model=AdmissionListDto, response_model_by_alias=True)
def read_candidate_admissions(request: Request, include: Literal["rejected"] | None = _INCLUDE) -> AdmissionListDto:
    """Every live admission record; a rejection stays readable as already tried, on request."""
    binding = bound_project(request.app.state)
    records, warnings = list_admissions(binding, include_rejected=include == "rejected")
    return AdmissionListDto(project_id=binding.project_id, admissions=[admission_dto(row) for row in records],
                            warnings=list(warnings))


@router.post("/admissions", response_model=CandidateAdmissionDto, response_model_by_alias=True, status_code=201)
def admit_candidate_results(request: Request, payload: AdmissionRequestDto, response: Response) -> CandidateAdmissionDto:
    """Retain one closed loop's verdict through the gate Stage acceptance also uses.

    201 retains a new record; 200 answers an identical retry with the record it
    repeats. Admission accepts no Stage and moves no Working Head.
    """
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    if request.app.state.settings.sync_url:
        raise StudioError(409, "SYNC_ADMISSION_UNSUPPORTED", "This Runtime is connected to a shared project, and "
                          "admissions are not synchronized yet; a local one would diverge from the shared project.")
    if payload.task.kind == TASK_HUB_CHAT and any(row.outcome == ADMITTED for row in payload.results):
        require_design_loop(request, "An Agent admits a result")
    record, created = admit_results(binding, payload.model_dump(by_alias=True, exclude={"project_id"}),
                                    _attribution(request), events=request.app.state.events)
    if not created:
        response.status_code = 200
    return admission_dto(record)


def require_design_loop(request: Request, what: str) -> None:
    """Refuse the Agent's own step onto the working design while the design loop is enforced (#185)."""

    if request.app.state.settings.design_loop_enforced:
        raise StudioError(409, "DESIGN_LOOP_REQUIRED", f"{what} only through POST /api/revisions while the design loop "
                          "is enforced: that call checks what the change may not touch, builds it, reads it back and "
                          "validates it before the result is admitted and continued. Nothing was retained.")


@router.post("/revisions", response_model=RevisionOutcomeDto, response_model_by_alias=True)
def revise_working_design(request: Request, payload: RevisionRequestDto) -> RevisionOutcomeDto:
    """One change carried through every check onto the working design, or stopped with the reason (#185).

    In order: the base must be the working design; the change becomes a proposal through its own route, with
    every keep decision protected; a keep conflict, a touched rejected option, a condition in reach that no check
    can verify, or a condition the built change drops or rewrites answers needs_question; the candidate is built
    and waited for, read back, compared and validated; then it is admitted and, unless land is false, continued.
    Nothing is built for a question, and nothing moves on problems.
    """

    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    if request.app.state.settings.sync_url:
        raise StudioError(409, "SYNC_REVISION_UNSUPPORTED", "This Runtime is connected to a shared project, and its "
                          "admissions are not synchronized yet.")
    message = None if payload.message_source is None else payload.message_source.model_dump(by_alias=True)
    outcome = revise(RevisionRequest(
        project_id=payload.project_id, state_digest=payload.state_digest, source_run_id=payload.source_run_id,
        kind=payload.kind, proposal=payload.proposal, summary=payload.summary, keep=tuple(payload.keep),
        confirmed=tuple(payload.confirmed), supersedes=tuple(payload.supersedes), land=payload.land,
        message_source=message, raw_language=payload.raw_language, label=payload.label,
        wait_s=float(payload.wait_seconds),
    ), _RevisionRoutes(request, message, payload.raw_language))
    return revision_dto(outcome)


@router.get("/revisions/context")
def read_revision_context(request: Request) -> dict:
    """What a change is written from: the working design, what the architect kept and rejected in their words,
    every live condition with whether a check can verify it and how it stands on the working design, the last
    landed change with its objects before and after, and the results the architect rejected. Bounded; nothing
    is written."""

    binding = bound_project(request.app.state)
    source = resolve_working_source(binding)
    head = source.head
    if head is None:
        raise StudioError(409, "WORKING_SOURCE_UNAVAILABLE", "The project has no working design to revise yet.")
    ports = _RevisionRoutes(request, None, None)
    record = ports.record(head.run_id)
    honesty: list[str] = []
    boxes = None
    if any(obligation_measured(row) for row in record.obligations):
        try:
            boxes = {shape.name: (tuple(shape.bbox_min), tuple(shape.bbox_max)) for shape in shapes_of(binding, head.run_id)
                     if shape.bbox_min is not None and shape.bbox_max is not None}
        except (StudioError, ProjectRepositoryError) as exc:
            honesty.append(f"conditions measured on the built model were not checked: {exc}")
    admitted, _ = list_admissions(binding, include_rejected=True)
    rows = [(record_row, row) for record_row in admitted for row in record_row.results]
    landed = next((pair for pair in reversed(rows) if pair[1]["runId"] == head.run_id and pair[1]["outcome"] == ADMITTED), None)
    last_change = None
    if landed is not None:
        last_change = {"runId": head.run_id, "summary": landed[1].get("summary") or landed[1].get("label"),
                       "words": landed[0].payload.get("rawLanguage"), "against": None, "objects": []}
        if len(head.lineage) > 1:
            last_change["against"] = head.lineage[1]
            try:
                comparison = compare_runs(binding, candidate_id=head.run_id, against=head.lineage[1], why=None,
                                          why_source="unavailable")
                last_change["objects"] = [
                    {"name": row.name, "status": row.status,
                     "before": None if row.before_min is None else {"min": list(row.before_min), "max": list(row.before_max)},
                     "after": None if row.after_min is None else {"min": list(row.after_min), "max": list(row.after_max)}}
                    for row in comparison.objects if row.status != "unchanged"][:40]
            except (StudioError, ProjectRepositoryError) as exc:
                honesty.append(f"the last change could not be compared with {head.lineage[1]}: {exc}")
    tried = [{"runId": row["runId"], "words": (record_row.payload.get("rawLanguage") or row.get("reason") or "")[:300]}
             for record_row, row in rows if row["outcome"] == "rejected"]
    return revision_context(
        working={"runId": head.run_id, "stateDigest": head.state_digest, "revisionSha256": source.revision_sha256,
                 "label": head.label},
        decisions=ports.decisions(head.run_id), record=record,
        results=check_obligations(record, boxes=boxes), last_change=last_change, tried=tried, honesty=honesty)


# The proposal route of each kind of change, with the body it takes.
_PROPOSERS: Mapping[str, tuple[Any, type[BaseModel]]] = {
    "construction": (create_construction_proposal, ConstructionRequestDto),
    "facets": (create_facets_proposal, FacetsRequestDto),
    "hosted-opening": (create_hosted_opening_proposal, HostedOpeningRequestDto),
    "elevation": (create_elevation_proposal, ElevationEditRequestDto),
    "proposal": (create_proposal, ProposalRequestDto),
}


class _RevisionRoutes:
    """The transaction's ports: the existing routes and functions, called inside this one request."""

    def __init__(self, request: Request, message: Mapping[str, str] | None, words: str | None) -> None:
        self.request, self.binding = request, bound_project(request.app.state)
        self.message, self.words = message, words

    def working_head(self) -> tuple[str | None, str | None, str | None]:
        source = resolve_working_source(self.binding)
        head = source.head
        return (None if head is None else head.run_id, None if head is None else head.state_digest,
                source.revision_sha256)

    def record(self, run_id: str):
        projection = project_state(self.binding, run_id=run_id)
        require_actionable(projection)
        return projection.record

    def decisions(self, run_id: str) -> tuple[Decision, ...]:
        """The design decisions that apply to this run's state: keep, reject and avoid, each with its words."""

        projection = project_state(self.binding, run_id=run_id)
        require_actionable(projection)
        record = projection.record
        stage = None if projection.source_stage_ref is None else projection.source_stage_ref.uri
        source = {"kind": "design", "sourceRunId": projection.run.run_id, "stateDigest": projection.state_digest,
                  "sourceStageRef": stage}
        refs = (*(f"entity:{row.entity_id}" for row in record.entities),
                *(f"parameter:{row.key}" for row in record.parameters),
                *(f"relation:{row.relation_id}" for row in record.relations))
        included, _ = compile_scoped_decisions(self.binding, DecisionContext(("design",), stage, refs, source), record)
        return tuple(Decision(row.decision_id, str(row.payload["disposition"]), str(row.payload["targetRef"]),
                              str(row.payload["rawLanguage"]))
                     for row in included if row.payload["disposition"] in ("keep", "reject", "avoid"))

    def propose(self, kind: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        route, contract = _PROPOSERS[kind]
        try:
            request_body = contract.model_validate(dict(body))
        except ValidationError as exc:
            raise StudioError(422, "PROPOSAL_INVALID", "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors())[:800]) from exc
        return route(self.request, request_body).model_dump(by_alias=True)

    def run(self, proposal_id: str) -> Mapping[str, Any]:
        return start_candidate(self.request, proposal_id).model_dump(by_alias=True)

    def job(self, job_id: str) -> Mapping[str, Any]:
        return read_job(self.request, job_id).model_dump(by_alias=True)

    def candidate(self, candidate_id: str) -> Mapping[str, Any]:
        return read_candidate(self.request, candidate_id).model_dump(by_alias=True)

    def compare(self, candidate_id: str, against: str) -> Mapping[str, Any]:
        return compare_candidate(self.request, candidate_id, against).model_dump(by_alias=True)

    def validation(self, candidate_id: str) -> Mapping[str, Any]:
        return read_validation(self.request, candidate_id).model_dump(by_alias=True)

    def admit(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = AdmissionRequestDto.model_validate(dict(body)).model_dump(by_alias=True, exclude={"project_id"})
        record, _ = admit_results(self.binding, spec, _attribution(self.request), events=self.request.app.state.events)
        return admission_dto(record).model_dump(by_alias=True)

    def continue_(self, run_id: str, revision: str | None) -> Mapping[str, Any]:
        answer = select_working_draft(self.binding, run_id, revision, attribution=_attribution(self.request),
                                      message_source=self.message, raw_language=self.words)
        retention = getattr(self.request.app.state, "retention", None)
        if retention is not None:
            retention.after_continue()
        return answer.model_dump(by_alias=True)


@router.post("/design-branches", response_model=DesignBranchDto, response_model_by_alias=True, status_code=201)
def fork_committed_design(request: Request, payload: ForkDesignBranchRequestDto) -> DesignBranchDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    if request.app.state.settings.sync_url:
        return DesignBranchDto.model_validate(fork_shared_branch(request.app.state, payload.model_dump(by_alias=True)))
    return branch_dto(fork_design_branch(binding, branch_id=payload.branch_id, parent_branch=payload.parent_branch,
                                         stage_ref=stage_ref_from(binding, payload.stage_ref)))


@router.get("/working-copies", response_model=WorkingCopyListDto, response_model_by_alias=True)
def read_working_copies(request: Request) -> WorkingCopyListDto:
    return WorkingCopyListDto(workingCopies=[working_copy_dto(item) for item in list_working_copies(bound_project(request.app.state))])


@router.get("/working-copies/{group_id}", response_model=WorkingCopyDto, response_model_by_alias=True)
def read_working_copy_group(request: Request, group_id: str, revision_sha256: str | None = Query(default=None, alias="revisionSha256")) -> WorkingCopyDto:
    return working_copy_dto(read_working_copy(bound_project(request.app.state), group_id, revision_sha256))


# Creating an Exploration is retired (#294 owner decision Q4): a Study is declared
# on a CandidateAdmission@1. Retained Explorations stay readable and selectable.


@router.put("/working-copies/{group_id}/selection", response_model=WorkingCopyDto, response_model_by_alias=True)
def choose_working_copy_option(request: Request, group_id: str, payload: WorkingCopySelectionRequestDto) -> WorkingCopyDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return working_copy_dto(select_working_copy_option(binding, group_id, payload.base_revision_sha256, payload.option_id))


@router.post("/working-copies/{group_id}/options", response_model=WorkingCopyDto, response_model_by_alias=True)
def append_working_copy_option(request: Request, group_id: str, payload: WorkingCopyOptionRequestDto) -> WorkingCopyDto:
    binding = bound_project(request.app.state)
    _require_bound_project(binding, payload.project_id)
    return working_copy_dto(add_working_copy_option(binding, group_id, payload.base_revision_sha256,
                                                   working_option_from(payload.option), event_sink=request.app.state.events))


@router.get(
    "/episodes",
    response_model=list[EpisodeDto],
    response_model_by_alias=True,
)
def list_episodes(
    request: Request,
    state_digest: str | None = Query(
        None,
        alias="stateDigest",
        description="only the judgements made against this exact state; "
        "left out, every judgement this process holds",
    ),
) -> list[EpisodeDto]:
    """Every judgement this process holds, in the order it made them."""

    return [
        episode_dto(episode)
        for episode in request.app.state.episodes.for_state(state_digest)
    ]


@router.get(
    "/episodes/{episode_id}",
    response_model=EpisodeDto,
    response_model_by_alias=True,
)
def read_episode(request: Request, episode_id: str) -> EpisodeDto:
    """One judgement, or a 404 that says where judgements do not survive."""

    return episode_dto(request.app.state.episodes.get(episode_id))
