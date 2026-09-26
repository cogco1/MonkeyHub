"""Observe exact models and generate their retained drawing revisions."""

import base64

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application.authentication import request_attribution
from ..application.binding import bound_project
from ..application.drawings import generate_elevation, generate_section_perspective, generate_sheet, model_view
from ..application.drawing_corrections import drawing_corrections
from ..application.drawing_plans import generate_plan, plan_status, plan_dimension_choices, dimension_proposal, plan_vector
from ..transport.artifacts import ModelSourceDto, SourceDocumentDto, document_dto, model_source_from
from ..transport.drawings import (
    DrawingCorrectionsDto, DrawingStylesDto, ElevationRequestDto, ModelViewDto, ModelViewName, SheetRequestDto,
    PlanRequestDto, PlanStatusRequestDto, PlanStatusDto,
    PlanDimensionChoicesDto, PlanDimensionProposalRequestDto, PlanVectorDto, SectionPerspectiveRequestDto,
)
from ..transport.errors import StudioError
from ..transport.proposal import ProposalDto, to_dto as proposal_dto

router = APIRouter(tags=["drawings"])


@router.post("/drawings/plans", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_plan(request: Request, payload: PlanRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    values = payload.model_dump(exclude={"project_id", "model_source", "source_asset", "dimensions", "dressing", "dressing_operations",
                                         "hatch", "beyond"})
    return document_dto(generate_plan(binding, **values, attribution=request_attribution(request),
        hatch=None if payload.hatch is None else payload.hatch.model_dump(by_alias=True),
        beyond=None if payload.beyond is None else payload.beyond.model_dump(by_alias=True),
        source_asset=None if payload.source_asset is None else payload.source_asset.model_dump(by_alias=True),
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        dimensions=None if payload.dimensions is None else [row.model_dump(by_alias=True) for row in payload.dimensions],
        dressing=None if payload.dressing is None else [row.model_dump(by_alias=True) for row in payload.dressing],
        dressing_operations=None if payload.dressing_operations is None else [row.model_dump(by_alias=True) for row in payload.dressing_operations]))


@router.get("/drawings/plans/vector", response_model=PlanVectorDto, response_model_by_alias=True)
def read_plan_vector(request: Request, run_id: str = Query(alias="runId", min_length=1),
                     asset_sha256: str = Query(alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
                     revision_ref: str = Query(alias="revisionRef", min_length=1)) -> PlanVectorDto:
    return PlanVectorDto(**plan_vector(bound_project(request.app.state), run_id=run_id,
                                      asset_sha256=asset_sha256, revision_ref=revision_ref))


@router.get("/drawings/corrections", response_model=DrawingCorrectionsDto, response_model_by_alias=True)
def read_drawing_corrections(request: Request, project_id: str = Query(alias="projectId", min_length=1),
                             drawing_id: str | None = Query(alias="drawingId", default=None,
                                                            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
                             ) -> DrawingCorrectionsDto:
    """Each revision of one cut plan beside the revision it continued, classified, and the project's repeated corrections.

    A read of the retained revisions and the active recipe decisions: the
    pairs, their classes and the suggestions are derived again on every call,
    and nothing is written. A suggestion is only an offer; saving it is a
    person's POST /api/decisions with a recipe binding, citing its page.
    """

    binding = bound_project(request.app.state)
    if project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return DrawingCorrectionsDto.model_validate(drawing_corrections(binding, drawing_id=drawing_id))


@router.post("/drawings/plans/status", response_model=PlanStatusDto, response_model_by_alias=True)
def read_plan_status(request: Request, payload: PlanStatusRequestDto) -> PlanStatusDto:
    return PlanStatusDto(**plan_status(bound_project(request.app.state),
        **payload.model_dump(exclude={"target_model_source"}),
        target_model_source=None if payload.target_model_source is None else model_source_from(payload.target_model_source)))


@router.get("/drawings/plans/dimensions", response_model=PlanDimensionChoicesDto, response_model_by_alias=True)
def read_plan_dimension_choices(request: Request,
    source_run_id: str | None = Query(alias="sourceRunId", default=None, min_length=1),
    state_digest: str | None = Query(alias="stateDigest", default=None, pattern=r"^[0-9a-f]{64}$"),
    asset_sha256: str | None = Query(alias="assetSha256", default=None, pattern=r"^[0-9a-f]{64}$"),
    source_stage_ref: str | None = Query(alias="sourceStageRef", default=None),
    source_asset_run_id: str | None = Query(alias="sourceAssetRunId", default=None, min_length=1),
    source_asset_sha256: str | None = Query(alias="sourceAssetSha256", default=None, pattern=r"^[0-9a-f]{64}$"),
) -> PlanDimensionChoicesDto:
    if source_asset_run_id is not None or source_asset_sha256 is not None:
        if not source_asset_run_id or not source_asset_sha256 or any((source_run_id, state_digest, asset_sha256, source_stage_ref)):
            raise StudioError(422, "DRAWING_SOURCE_AMBIGUOUS", "Choose one complete source asset or model binding.")
        return PlanDimensionChoicesDto(**plan_dimension_choices(bound_project(request.app.state),
            source_asset={"runId": source_asset_run_id, "assetSha256": source_asset_sha256}))
    if not all((source_run_id, state_digest, asset_sha256)):
        raise StudioError(422, "DRAWING_SOURCE_REQUIRED", "Provide an exact model or imported asset.")
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
    view: ModelViewName = Query(default="front"),
) -> ModelViewDto:
    """Observe an exact complete model without creating a run, drawing or project record.

    The image is a visible-line orthographic projection, not a material render;
    top is an uncut projection, not a floor plan, and axon is the isometric
    view from the -X, -Y, +Z side with Z up. Its longest edge is at most 1024
    pixels. A repeated view of the same exact source is reused from process
    memory after the source verifies again.
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
        source_asset=None if payload.source_asset is None else payload.source_asset.model_dump(by_alias=True),
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        style_id=payload.style_id, scale_denominator=payload.scale_denominator,
        hidden_object_ids=tuple(payload.hidden_object_ids), outline_object_ids=tuple(payload.outline_object_ids),
        notes=tuple(payload.notes), monitor=request.app.state.monitor,
    ))


@router.post("/drawings/section-perspectives", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_section_perspective(request: Request, payload: SectionPerspectiveRequestDto) -> SourceDocumentDto:
    """Retain a true section perspective (剖透视) of an exact model and register it in the documents list.

    The section plane cuts the retained STEP or registered native model; the side away from the eye
    is drawn in exact perspective with visible lines only, and the cut is
    filled (poché). The picture plane is the section plane, so the cut is true
    to scale at 1:scaleDenominator and lines perpendicular to it converge at
    the eye's foot on the plane. The minimal request is a plan line and the
    kept side; the default camera looks straight through the cut. Coordinates
    use the source model's CAD frame and length unit (X/Y plan, Z up). Refusals
    are named: SECTION_PLANE_MISSES_MODEL, SECTION_EYE_ON_KEPT_SIDE,
    SECTION_EYE_ON_PLANE, SECTION_CAMERA_DEGENERATE, SECTION_LINE_DEGENERATE,
    SECTION_NORMAL_DEGENERATE, SECTION_DEPTH_INVALID, SECTION_VALUE_NOT_FINITE,
    SECTION_REQUEST_INVALID, DRAWING_OBJECT_UNKNOWN and DRAWING_EMPTY.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    graphics = {key: value for key, value in (("cutLineMm", payload.cut_line_mm), ("visibleLineMm", payload.visible_line_mm),
                                              ("hatchSpacingMm", payload.hatch_spacing_mm)) if value is not None}
    return document_dto(generate_section_perspective(
        binding, source_stage_ref=payload.source_stage_ref,
        source_asset=None if payload.source_asset is None else payload.source_asset.model_dump(by_alias=True),
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        section=payload.section.model_dump(by_alias=True),
        camera=None if payload.camera is None else payload.camera.model_dump(by_alias=True, exclude_none=True),
        depth=payload.depth, hidden_object_ids=tuple(payload.hidden_object_ids), drawing_id=payload.drawing_id,
        scale_denominator=payload.scale_denominator, graphics=graphics or None,
        hatch=None if payload.hatch is None else payload.hatch.model_dump(by_alias=True, exclude_none=True),
        beyond=None if payload.beyond is None else payload.beyond.model_dump(), monitor=request.app.state.monitor,
    ))


@router.post("/drawings/elevations", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_elevation(request: Request, payload: ElevationRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_elevation(
        binding, source_stage_ref=payload.source_stage_ref,
        source_asset=None if payload.source_asset is None else payload.source_asset.model_dump(by_alias=True),
        model_source=None if payload.model_source is None else model_source_from(payload.model_source),
        view=payload.view, drawing_id=payload.drawing_id, hidden_lines=payload.hidden_lines,
        scale_denominator=payload.scale_denominator,
        monitor=request.app.state.monitor,
    ))
