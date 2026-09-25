"""One project publication, immutable revisions and bounded byte exports."""
from fastapi import APIRouter
from fastapi.responses import Response
from starlette.requests import Request
from ..application import publications
from ..application.publication_output import export_publication
from ..application.binding import bound_project
from ..transport.publications import PublicationDto, PublicationRequestDto, PublicationExportRequestDto, PublicationBoardRequestDto
from ..transport.errors import StudioError
from .artifacts import _content_disposition

router = APIRouter(tags=["publication"])


def _binding(request, project_id):
    binding = bound_project(request.app.state)
    if project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The publication names another project.")
    return binding


@router.get("/publication", response_model=PublicationDto, response_model_by_alias=True)
def read_publication(request: Request, revision: str | None = None):
    return publications.read_publication(bound_project(request.app.state), revision)


@router.put("/publication", response_model=PublicationDto, response_model_by_alias=True)
def save_publication(request: Request, payload: PublicationRequestDto):
    return publications.save_publication(_binding(request, payload.project_id), payload.base_revision_sha256,
        payload.title, payload.spec.model_dump(by_alias=True, mode="json"), [page.model_dump(by_alias=True, mode="json") for page in payload.pages])


@router.post("/publication/from-board", response_model=PublicationDto, response_model_by_alias=True)
def publication_from_board(request: Request, payload: PublicationBoardRequestDto):
    return publications.append_board_selection(_binding(request, payload.project_id), payload.base_revision_sha256,
        payload.board_revision_sha256, payload.element_ids)


@router.post("/publication/export", response_class=Response)
def export(request: Request, payload: PublicationExportRequestDto):
    result = export_publication(_binding(request, payload.project_id), payload.revision_sha256, payload.format)
    return Response(result.content, media_type=result.media_type,
        headers={"Content-Disposition": _content_disposition(result.file_name, payload.revision_sha256), "Cache-Control": "no-store"})
