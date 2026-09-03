"""``GET /api/artifacts``: what the run receipts certify, and its bytes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from starlette.requests import Request

from ..application.artifacts import artifact_bytes, list_artifacts
from ..application.binding import bound_project
from ..transport.artifacts import ArtifactListDto, to_dto

router = APIRouter(tags=["artifacts"])


@router.get(
    "/artifacts",
    response_model=ArtifactListDto,
    response_model_by_alias=True,
)
def read_artifacts(request: Request) -> ArtifactListDto:
    """List the exported models, each one still answered for by its receipt."""

    return to_dto(list_artifacts(bound_project(request.app.state)))


# The one route that does not answer with a DTO: its body is the exported file
# itself, served only after those bytes hash to the digest in the path.
@router.get("/artifacts/{sha256}/bytes", response_class=Response)
def read_artifact_bytes(request: Request, sha256: str) -> Response:
    """The certified bytes, under the digest that identifies them."""

    record, data = artifact_bytes(bound_project(request.app.state), sha256)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            # The digest is the identity, so it is the entity tag.
            "ETag": f'"{record.sha256}"',
            "Content-Disposition": (
                f'attachment; filename="{_header_safe(record.file_name)}"'
            ),
            # Never cached: the next request must verify the file again.
            "Cache-Control": "no-store",
        },
    )


def _header_safe(file_name: str) -> str:
    """The file name as a header value can carry it.

    File names come off a receipt, so a quote or a newline in one is a project
    on disk deciding this response's headers. The name the client should show
    travels as ``fileName`` in the listing; here it is only a save-as hint.
    """

    return "".join(
        character
        for character in file_name
        if character.isprintable() and character not in '"\\'
    )
