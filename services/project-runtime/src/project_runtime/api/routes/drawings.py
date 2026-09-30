"""Observe exact models and generate their retained drawing revisions."""

import base64
import hashlib

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response
from starlette.requests import Request

from ...authentication import request_attribution
from ...binding import bound_project
from ...application.drawings import drawing_file, generate_elevation, generate_section_perspective, generate_sheet, model_view
from ...application.drawing_corrections import drawing_corrections
from ...application.drawing_plans import generate_plan, plan_status, plan_dimension_choices, dimension_proposal, plan_vector
from ..dto.artifacts import ModelSourceDto, SourceDocumentDto, document_dto, model_source_from
from ..dto.drawings import (
    DrawingCorrectionsDto, DrawingFileFormat, DrawingStylesDto, ElevationDrawingDto, ElevationRequestDto, ModelViewDto,
    ModelViewName, SheetRequestDto, SheetViewDto,
    PlanDrawingDto, PlanRequestDto, PlanStatusRequestDto, PlanStatusDto,
    PlanDimensionChoicesDto, PlanDimensionProposalRequestDto, PlanVectorDto, SectionPerspectiveDrawingDto,
    SectionPerspectiveRequestDto,
)
from ...errors import StudioError
from ..dto.proposal import ProposalDto, to_dto as proposal_dto
from .artifacts import IMMUTABLE, _content_disposition
from .projections import ready_projections

router = APIRouter(tags=["drawings"])


def _source(payload) -> dict:
    """The one exact source a drawing request names, as the generators take it."""
    return {"source_stage_ref": payload.source_stage_ref,
            "source_asset": None if payload.source_asset is None else payload.source_asset.model_dump(by_alias=True),
            "model_source": None if payload.model_source is None else model_source_from(payload.model_source)}


def plan_arguments(payload: PlanDrawingDto) -> dict:
    """A cut plan's or vertical section's own fields as ``generate_plan`` takes them."""
    values = payload.model_dump(include=set(PlanDrawingDto.model_fields) - {
        "dimensions", "dressing", "dressing_operations", "hatch", "beyond"})
    return {**values,
            "hatch": None if payload.hatch is None else payload.hatch.model_dump(by_alias=True),
            "beyond": None if payload.beyond is None else payload.beyond.model_dump(by_alias=True),
            "dimensions": None if payload.dimensions is None else [row.model_dump(by_alias=True) for row in payload.dimensions],
            "dressing": None if payload.dressing is None else [row.model_dump(by_alias=True) for row in payload.dressing],
            "dressing_operations": None if payload.dressing_operations is None else [
                row.model_dump(by_alias=True) for row in payload.dressing_operations]}


def elevation_arguments(payload: ElevationDrawingDto) -> dict:
    """An elevation's or axonometric's own fields as ``generate_elevation`` takes them."""
    return {"view": payload.view, "direction": payload.direction, "drawing_id": payload.drawing_id,
            "hidden_lines": payload.hidden_lines, "scale_denominator": payload.scale_denominator,
            "length_unit": payload.length_unit}


def section_perspective_arguments(payload: SectionPerspectiveDrawingDto) -> dict:
    """A section perspective's own fields as ``generate_section_perspective`` takes them."""
    graphics = {key: value for key, value in (("cutLineMm", payload.cut_line_mm), ("visibleLineMm", payload.visible_line_mm),
                                              ("hatchSpacingMm", payload.hatch_spacing_mm)) if value is not None}
    return {"section": payload.section.model_dump(by_alias=True),
            "camera": None if payload.camera is None else payload.camera.model_dump(by_alias=True, exclude_none=True),
            "depth": payload.depth, "hidden_object_ids": tuple(payload.hidden_object_ids), "drawing_id": payload.drawing_id,
            "scale_denominator": payload.scale_denominator, "graphics": graphics or None,
            "hatch": None if payload.hatch is None else payload.hatch.model_dump(by_alias=True, exclude_none=True),
            "beyond": None if payload.beyond is None else payload.beyond.model_dump(), "length_unit": payload.length_unit}


def _sheet_view(view: SheetViewDto) -> dict:
    """One placed view: where it goes and its own route's arguments, the drawing identity being the view's id."""
    if view.plan is not None:
        kind, arguments = "plan", plan_arguments(view.plan)
    elif view.elevation is not None:
        kind, arguments = "elevation", elevation_arguments(view.elevation)
    else:
        kind, arguments = "section-perspective", section_perspective_arguments(view.section_perspective)
    arguments.pop("drawing_id", None)
    return {"id": view.id, "kind": kind, "arguments": arguments, "place_mm": list(view.place_mm), "title": view.title,
            "subtitle": view.subtitle, "mark_on": view.mark_on, "mark_label": view.mark_label}


@router.post("/drawings/plans", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_plan(request: Request, payload: PlanRequestDto) -> SourceDocumentDto:
    """Retain a horizontal cut plan, or with section a vertical model-axis section, and register it in the documents list.

    Both are one composition over the exact source (the cut filled, what lies beyond it drawn); a rebuild keeps its
    orientation, plane, kept side and depth. Refusals are named: SECTION_PLANE_NOT_MODEL_AXIS,
    SECTION_PLANE_MISSES_MODEL, SECTION_LINE_DEGENERATE, SECTION_NORMAL_DEGENERATE, DRAWING_ORIENTATION_CHANGED,
    DRAWING_KIND_CHANGED (the id names another kind of drawing), DRAWING_SECTION_ANNOTATION_INVALID,
    DRAWING_UNIT_MISMATCH and the cut plan's own.
    """
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_plan(binding, **plan_arguments(payload), **_source(payload),
                                      attribution=request_attribution(request)))


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
    """Compose one sheet of one exact source, retained as PDF, DXF, SVG and PNG and registered as its PDF.

    Without views: front, right and top at one scale in the style's layout. With views: each view is drawn, or its
    registered revision read back, through its own route's owner from this request's source, then placed at its own
    scale at placeMm; its revision is named in the sheet's viewRecipe.views. Read the sheet's files with
    GET /api/drawings/{assetSha256}/files/{format}. Refusals are named: DRAWING_SHEET_LAYOUT_INVALID,
    DRAWING_VIEW_EMPTY, DRAWING_SHEET_VIEW_UNSUPPORTED, DRAWING_SECTION_MARK_INVALID, DRAWING_SECTION_MARK_OUTSIDE,
    DRAWING_UNIT_MISMATCH, and each view's own, prefixed with its id. A refused sheet registers no sheet, but the views
    drawn before the refusal stay registered as their own routes register them, including a view refused here for
    drawing nothing; the same request again reads those views back rather than drawing them again.
    """
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_sheet(
        binding, **_source(payload),
        style_id=payload.style_id, scale_denominator=payload.scale_denominator,
        hidden_object_ids=tuple(payload.hidden_object_ids), outline_object_ids=tuple(payload.outline_object_ids),
        notes=tuple(payload.notes), monitor=request.app.state.monitor, projections=ready_projections(request.app.state),
        views=None if payload.views is None else tuple(_sheet_view(view) for view in payload.views),
        paper_size_mm=payload.paper_size_mm, title=payload.title, subtitle=payload.subtitle,
        sheet_number=payload.sheet_number, drawing_id=payload.drawing_id, length_unit=payload.length_unit,
        attribution=request_attribution(request),
    ))


@router.get("/drawings/{asset_sha256}/files/{file_format}", response_class=Response,
            responses={200: {"content": {"application/pdf": {}, "application/dxf": {}, "image/svg+xml": {}, "image/png": {}}}})
def read_drawing_file(request: Request, asset_sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
                      file_format: DrawingFileFormat = Path(description=(
                          "svg or png of a view drawing; pdf, dxf, svg or png of a sheet: the same paper scene.")),
                      run_id: str = Query(alias="runId", min_length=1),
                      revision_ref: str | None = Query(default=None, alias="revisionRef")) -> Response:
    """One retained file of a registered drawing, read and verified, never drawn again.

    A view drawing (plan, section, elevation, axonometric, section perspective) has its SVG and PNG; a sheet has its
    PDF and the DXF, SVG and PNG of the same paper scene, which its PDF names by digest. Address it as the documents
    list does: assetSha256, runId and, for a view, its revisionRef, which a view's files need
    (DRAWING_REVISION_REQUIRED): two revisions can share a PNG yet differ in SVG. DRAWING_FILE_UNAVAILABLE names a
    file a drawing does not have, including a sheet retained before its PDF named its other files.
    """
    data, media_type, file_name = drawing_file(bound_project(request.app.state), run_id=run_id, asset_sha256=asset_sha256,
                                               revision_ref=revision_ref, file_format=file_format)
    headers = {"ETag": f'"{hashlib.sha256(data).hexdigest()}"', "Cache-Control": IMMUTABLE,
               "Content-Disposition": _content_disposition(file_name, asset_sha256), "X-Content-Type-Options": "nosniff"}
    if file_format == "svg":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src data:"
    return Response(content=data, media_type=media_type, headers=headers)


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
    return document_dto(generate_section_perspective(
        binding, **_source(payload), **section_perspective_arguments(payload), monitor=request.app.state.monitor,
        projections=ready_projections(request.app.state),
    ))


@router.post("/drawings/elevations", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_elevation(request: Request, payload: ElevationRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The drawing names another project.")
    return document_dto(generate_elevation(
        binding, **_source(payload), **elevation_arguments(payload),
        monitor=request.app.state.monitor, projections=ready_projections(request.app.state),
    ))
