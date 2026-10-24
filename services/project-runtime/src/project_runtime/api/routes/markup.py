"""``POST /api/markup/resolve``: what each mark the architect drew lies on (#300 3-2)."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ...binding import bound_project
from ...application.markup import MODEL_ZONE, PageMarks, hit_subjects_of, read_model_marks, read_page_marks
from ...application.projection import project_state
from ...errors import StudioError
from ..dto.intent import document_gesture_from, gesture_from
from ..dto.markup import MarkupReadingsDto, MarkupResolveRequestDto, markup_readings_dto
from .proposals import _stale_base

router = APIRouter(tags=["markup"])


@router.post(
    "/markup/resolve",
    response_model=MarkupReadingsDto,
    response_model_by_alias=True,
)
def resolve(request: Request, body: MarkupResolveRequestDto) -> MarkupReadingsDto:
    """Read each mark into the components and elements it lies on, and say when one is unclear.

    Marks on a registered page are read against the page as it was drawn: a
    view drawing's or view sheet's object-named lines, or a camera pass's
    object-ID image, answered for by the record state the drawing was made
    from. Strokes on the model are read by the objects their samples fell on,
    answered for as a click is (``/api/pick/resolve``) against the state the
    model view was loaded with. Text is the sentence, not a mark. Nothing is
    written and no model is called; the same marks read the same every time.
    """

    binding = bound_project(request.app.state)
    if body.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The marks name another project.")
    if body.page is not None:
        page = body.page
        return markup_readings_dto(read_page_marks(
            binding, page.run_id, page.asset_sha256, page.page_index,
            tuple(document_gesture_from(mark) for mark in body.marks), revision_ref=page.drawing_revision_ref))
    projection = project_state(binding, run_id=body.source_run_id)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the strokes", body.source_run_id)
    readings = read_model_marks([(stroke.id, gesture_from(stroke)) for stroke in body.strokes],
                                hit_subjects_of(projection))
    return markup_readings_dto(PageMarks(MODEL_ZONE, projection.state_digest, readings))
