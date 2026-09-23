"""Observe exact models and generate their retained drawing revisions."""

import base64
from typing import Literal

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.drawings import generate_elevation, generate_sheet, model_view
from ..application.drawing_plans import generate_plan, plan_status, plan_dimension_choices, dimension_proposal
from ..transport.artifacts import ModelSourceDto, SourceDocumentDto, document_dto, model_source_from
from ..transport.drawings import (
    DrawingStylesDto, ElevationRequestDto, ModelViewDto, SheetRequestDto,
    PlanRequestDto, PlanStatusRequestDto, PlanStatusDto,
    PlanDimensionChoicesDto, PlanDimensionProposalRequestDto,
)
from ..transport.errors import StudioError
from ..transport.proposal import ProposalDto, to_dto as proposal_dto

router = APIRouter(tags=["drawings"])


@router.post("/drawings/plans", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_plan(request: Request, payload: PlanRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    values = payload.model_dump(exclude={"project_id", "model_source", "dimensions"})
    return document_dto(generate_plan(binding, **values,
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        dimensions=None if payload.dimensions is None else [row.model_dump(by_alias=True) for row in payload.dimensions]))


@router.post("/drawings/plans/status", response_model=PlanStatusDto, response_model_by_alias=True)
def read_plan_status(request: Request, payload: PlanStatusRequestDto) -> PlanStatusDto:
    return PlanStatusDto(**plan_status(bound_project(request.app.state),
        **payload.model_dump(exclude={"target_model_source"}),
        target_model_source=None if payload.target_model_source is None else model_source_from(payload.target_model_source)))


@router.get("/drawings/plans/dimensions", response_model=PlanDimensionChoicesDto, response_model_by_alias=True)
def read_plan_dimension_choices(request: Request,
    source_run_id: str = Query(alias="sourceRunId", min_length=1),
    state_digest: str = Query(alias="stateDigest", pattern=r"^[0-9a-f]{64}$"),
    asset_sha256: str = Query(alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
    source_stage_ref: str | None = Query(alias="sourceStageRef", default=None),
) -> PlanDimensionChoicesDto:
    source = model_source_from(ModelSourceDto(run_id=source_run_id, state_digest=state_digest, asset_sha256=asset_sha256))
    return PlanDimensionChoicesDto(**plan_dimension_choices(bound_project(request.app.state), source, source_stage_ref))


@router.post("/drawings/plans/dimension-proposal", response_model=ProposalDto, response_model_by_alias=True, status_code=201)
def create_plan_dimension_proposal(request: Request, payload: PlanDimensionProposalRequestDto) -> ProposalDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    proposal = dimension_proposal(binding,
        **payload.model_dump(exclude={"project_id", "target_model_source"}),
        target_model_source=None if payload.target_model_source is None else model_source_from(payload.target_model_source))
    return proposal_dto(request.app.state.proposals.put(proposal))


@router.get("/drawings/model-view", response_model=ModelViewDto, response_model_by_alias=True)
def read_model_view(
    request: Request,
    run_id: str = Query(alias="runId", min_length=1),
    state_digest: str = Query(alias="stateDigest", pattern=r"^[0-9a-f]{64}$"),
    asset_sha256: str = Query(alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
    view: Literal["front", "back", "left", "right", "top"] = Query(default="front"),
) -> ModelViewDto:
    """Observe an exact complete model without creating a run, drawing or project record.

    The image is a visible-line orthographic projection, not a material render;
    top is an uncut projection, not a floor plan. Its longest edge is at most 1024 pixels.
    """

    source = ModelSourceDto(run_id=run_id, state_digest=state_digest, asset_sha256=asset_sha256)
    png, width, height = model_view(bound_project(request.app.state), model_source=model_source_from(source), view=view)
    return ModelViewDto(source=source, view=view, data=base64.b64encode(png).decode("ascii"), width=width, height=height)


@router.get("/drawings/styles", response_model=DrawingStylesDto, response_model_by_alias=True)
def read_drawing_styles() -> DrawingStylesDto:
    from monkeydiagram.documentation.styles import list_drawing_styles

    return DrawingStylesDto(styles=list_drawing_styles())


@router.post("/drawings/sheets", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_sheet(request: Request, payload: SheetRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_sheet(
        binding, source_stage_ref=payload.source_stage_ref,
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        style_id=payload.style_id, scale_denominator=payload.scale_denominator,
        hidden_object_ids=tuple(payload.hidden_object_ids), outline_object_ids=tuple(payload.outline_object_ids),
        notes=tuple(payload.notes), monitor=request.app.state.monitor,
    ))


@router.post("/drawings/elevations", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_elevation(request: Request, payload: ElevationRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_elevation(
        binding, source_stage_ref=payload.source_stage_ref,
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        view=payload.view, drawing_id=payload.drawing_id, hidden_lines=payload.hidden_lines,
        scale_denominator=payload.scale_denominator,
        monitor=request.app.state.monitor,
    ))
