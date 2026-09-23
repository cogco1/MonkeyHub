"""Save, read and revise the bound project's scoped decisions."""

from fastapi import APIRouter
from starlette.requests import Request

from ..application import decisions
from ..application.authentication import request_attribution
from ..application.binding import bound_project
from ..transport.decisions import (
    DecisionDto,
    DecisionHistoryDto,
    DecisionListDto,
    DecisionRequestDto,
    DecisionRevisionRequestDto,
    decision_dto,
)
from ..transport.errors import StudioError

router = APIRouter(tags=["decisions"])


def _binding(request: Request, project_id: str):
    binding = bound_project(request.app.state)
    if project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The decision names another project.")
    return binding


@router.get("/decisions", response_model=DecisionListDto, response_model_by_alias=True)
def read_decisions(request: Request) -> DecisionListDto:
    """Every decision this project retains, at its current revision."""

    binding = bound_project(request.app.state)
    return DecisionListDto(
        projectId=binding.project_id,
        decisions=[decision_dto(row) for row in decisions.list_decisions(binding)],
    )


@router.get("/decisions/{decision_id}", response_model=DecisionHistoryDto, response_model_by_alias=True)
def read_decision(request: Request, decision_id: str) -> DecisionHistoryDto:
    """One decision's whole chain: every revision as it was written."""

    binding = bound_project(request.app.state)
    chain = decisions.decision_history(binding, decision_id)
    return DecisionHistoryDto(
        projectId=binding.project_id,
        decisionId=decision_id,
        # Every revision but the tip has been superseded. That is what reading
        # the chain establishes; no record was rewritten to say it.
        revisions=[decision_dto(row, status=None if row is chain[-1] else "superseded")
                   for row in chain],
    )


@router.post("/decisions", response_model=DecisionDto, response_model_by_alias=True, status_code=201)
def create_decision(request: Request, payload: DecisionRequestDto) -> DecisionDto:
    """Retain one decision, checked against the exact source it names."""

    binding = _binding(request, payload.project_id)
    return decision_dto(decisions.save_decision(
        binding, payload.model_dump(by_alias=True), request_attribution(request),
    ))


@router.post("/decisions/{decision_id}/revisions", response_model=DecisionDto,
             response_model_by_alias=True, status_code=201)
def revise_decision(request: Request, decision_id: str, payload: DecisionRevisionRequestDto) -> DecisionDto:
    """Revoke or supersede one decision; its previous wording is kept as written."""

    binding = _binding(request, payload.project_id)
    return decision_dto(decisions.revise_decision(
        binding, decision_id,
        expected_revision_ref=payload.expected_revision_ref,
        action=payload.action,
        reason=payload.reason,
        replacement=None if payload.replacement is None else payload.replacement.model_dump(by_alias=True),
        attribution=request_attribution(request),
        revision_message_source=(None if payload.revision_message_source is None
                                 else payload.revision_message_source.model_dump(by_alias=True)),
    ))
