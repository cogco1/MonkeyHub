"""Read and save the bound project's single MonkeyBoard."""

from fastapi import APIRouter
from fastapi.responses import Response
from starlette.requests import Request

from ...application import boards
from ...binding import bound_project
from ..dto.boards import (BoardDto, BoardExportPageDto, BoardExportRequestDto, BoardRequestDto, BoardViewDto,
                          BoardViewRequestDto, board_dto, board_view_dto)
from ...errors import StudioError
from .artifacts import _content_disposition
from .projections import ready_projections

router = APIRouter(tags=["board"])


@router.get("/board", response_model=BoardDto, response_model_by_alias=True)
def read_board(request: Request) -> BoardDto:
    return board_dto(boards.read_board(bound_project(request.app.state)))


@router.put("/board", response_model=BoardDto, response_model_by_alias=True)
def update_board(request: Request, payload: BoardRequestDto) -> BoardDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The board names another project.")
    return board_dto(boards.save_board(binding, payload.base_revision_sha256, payload.title,
                                      payload.elements, payload.seen_documents))


@router.post("/board/view", response_model=BoardViewDto, response_model_by_alias=True)
def view_board(request: Request, payload: BoardViewRequestDto) -> BoardViewDto:
    """Draw one frame or region of the board with what is drawn on its pages, and list those marks (a read)."""

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The board view names another project.")
    region = None if payload.region is None else (payload.region.x, payload.region.y,
                                                  payload.region.width, payload.region.height)
    view = boards.view_board(binding, ready_projections(request.app.state), frame_id=payload.frame_id, region=region,
                             max_edge=payload.max_edge, revision_sha256=payload.revision_sha256)
    return board_view_dto(binding.project_id, view)


@router.post("/board/export", response_class=Response)
def export_board(request: Request, payload: BoardExportRequestDto) -> Response:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The board export names another project.")
    result = boards.export_board_pages(
        binding,
        [boards.BoardExportPage(page.run_id, page.asset_sha256, page.revision_ref, page.page_index) for page in payload.pages],
        payload.format,
        payload.zip,
        payload.max_edge,
    )
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={"Content-Disposition": _content_disposition(result.file_name, payload.pages[0].asset_sha256), "Cache-Control": "no-store"},
    )
