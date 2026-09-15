"""Evidence-grounded precedent Study routes."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.study import read_study, save_study
from ..transport.errors import StudioError
from ..transport.study import SaveStudyRequestDto, StudyViewDto, study_dto


router = APIRouter(tags=["study"])


@router.post("/studies", response_model=StudyViewDto, response_model_by_alias=True, status_code=201)
def retain_study(request: Request, payload: SaveStudyRequestDto) -> StudyViewDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The Study names another project.")
    source = payload.source
    return study_dto(save_study(
        binding,
        study_id=payload.study_id,
        source_run_id=source.run_id,
        asset_sha256=source.asset_sha256,
        revision_ref=source.revision_ref,
        page_index=source.page_index,
        evidence_rows=(row.model_dump() for row in payload.evidence),
        expected_previous_ref=payload.expected_previous_ref,
    ))


@router.get("/studies/{study_id}", response_model=StudyViewDto, response_model_by_alias=True)
def reopen_study(
    request: Request,
    study_id: str,
    ledger_ref: str | None = Query(default=None, alias="ledgerRef"),
) -> StudyViewDto:
    return study_dto(read_study(bound_project(request.app.state), study_id, ledger_ref))
