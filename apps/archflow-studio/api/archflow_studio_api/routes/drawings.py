"""Generate one exact elevation and expose its retained document revision."""

from fastapi import APIRouter
from starlette.requests import Request

from ..application.binding import bound_project
from ..application.drawings import generate_elevation, generate_sheet
from ..transport.artifacts import SourceDocumentDto, document_dto, model_source_from
from ..transport.drawings import DrawingStylesDto, ElevationRequestDto, SheetRequestDto
from ..transport.errors import StudioError

router = APIRouter(tags=["drawings"])


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
