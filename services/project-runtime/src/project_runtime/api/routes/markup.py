"""``POST /api/markup/resolve``: what each mark the architect drew lies on (#300 3-2)."""

from __future__ import annotations

from fastapi import APIRouter, Path
from starlette.requests import Request

from ...binding import bound_project
from ...application import artifacts
from ...application.markup import MODEL_ZONE, PageMarks, hit_subjects_of, read_model_marks, read_page_marks
from ...application.model_parts import model_parts
from ...application.projection import project_state
from ...errors import StudioError
from ..dto.intent import document_gesture_from, gesture_from
from ..dto.markup import MarkupReadingsDto, MarkupResolveRequestDto, markup_readings_dto
from .projections import ready_projections
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
            tuple(document_gesture_from(mark) for mark in body.marks), revision_ref=page.drawing_revision_ref,
            parts=lambda sha: attached_parts(request, sha)))
    projection = project_state(binding, run_id=body.source_run_id)
    if body.state_digest != projection.state_digest:
        raise _stale_base(binding, projection, body.state_digest, "the strokes", body.source_run_id)
    readings = read_model_marks([(stroke.id, gesture_from(stroke)) for stroke in body.strokes],
                                hit_subjects_of(projection))
    return markup_readings_dto(PageMarks(MODEL_ZONE, projection.state_digest, readings))


def attached_parts(request: Request, asset_sha256: str) -> dict:
    """What each object of a model attached from outside the project is (``model_parts``), kept per file."""

    binding = bound_project(request.app.state)
    _, data = artifacts.artifact_bytes(binding, asset_sha256, run_id=f"studio-model-{asset_sha256}")
    return model_parts(data, asset_sha256, projections=ready_projections(request.app.state),
                       compiler=request.app.state.intent_compiler)


@router.get("/model-assets/{asset_sha256}/parts")
def read_model_asset_parts(request: Request, asset_sha256: str = Path(pattern=r"^[0-9a-f]{64}$")) -> dict:
    """Each object of a model attached from outside the project (a Live Rhino snapshot), read as a building part.

    By its own name, its layer's name, then its shape; the groups still unread
    are named by the project's configured model, and are ``other`` without one.
    ``objects`` maps an object id to ``{part, name, by}``; ``parts`` counts
    each part with its labels; ``recognized`` is the share read as a part. The
    reading is kept by the file's bytes, so a model is asked about once.
    """

    return attached_parts(request, asset_sha256)
