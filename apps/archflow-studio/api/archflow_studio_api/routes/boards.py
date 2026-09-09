"""Read and save the bound project's single MonkeyBoard."""

from fastapi import APIRouter
from starlette.requests import Request

from ..application import boards
from ..application.binding import bound_project
from ..transport.boards import BoardDto, BoardRequestDto, board_dto
from ..transport.errors import StudioError

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
