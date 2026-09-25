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

from typing import Literal

from fastapi import APIRouter, Query, Response
from starlette.requests import Request

from ..transport.proposal import EpisodeDto, episode_dto
from ..application.binding import bound_project
from ..application.authentication import ActorAttribution, request_attribution
from ..application.synchronization import accept_shared_candidate, fork_shared_branch
from ..transport.errors import StudioError
from ..application.monitoring import candidate_event_id
from ..application.design_history import (
    accept_design_candidate, admit_results, fork_design_branch, initialize_design_stage,
    list_admissions, read_design_history, stage_ref_from,
)
from ..application.episodes import (
    add_working_copy_option, list_working_copies,
    read_working_copy, select_working_copy_option,
)
from ..transport.artifacts import model_source_from
from ..transport.design_history import (
    AcceptDesignCandidateRequestDto, AdmissionListDto, AdmissionRequestDto, CandidateAdmissionDto,
    DesignBranchDto, DesignHistoryDto, DesignStageDto,
    ForkDesignBranchRequestDto, InitializeDesignStageRequestDto,
    admission_dto, branch_dto, history_dto, stage_dto,
)
from ..transport.proposal import (
    WorkingCopyDto, WorkingCopyListDto,
    WorkingCopySelectionRequestDto, WorkingCopyOptionRequestDto,
    working_copy_dto, working_option_from,
)
from .proposals import _require_bound_project

_INCLUDE = Query(default=None, description="rejected also lists retained rejections, for advanced views.")

router = APIRouter(tags=["episodes"])


def _attribution(request: Request) -> ActorAttribution:
    """Who is accepting, from the request boundary rather than from its body."""

    return request_attribution(request)


@router.get("/design-history", response_model=DesignHistoryDto, response_model_by_alias=True)
def read_committed_design_history(request: Request, branch_id: str = Query(default="main", alias="branchId"),
                                  include: Literal["rejected"] | None = _INCLUDE) -> DesignHistoryDto:
    """Committed Stages of one line, and the project's admitted Candidates and Studies."""
    return history_dto(read_design_history(bound_project(request.app.state), branch_id,
                                           include_rejected=include == "rejected"))


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
    record, created = admit_results(binding, payload.model_dump(by_alias=True, exclude={"project_id"}),
                                    _attribution(request), events=request.app.state.events)
    if not created:
        response.status_code = 200
    return admission_dto(record)


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
