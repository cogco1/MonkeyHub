"""``GET /api/artifacts``: what the run receipts certify, and its bytes."""

from __future__ import annotations

from urllib.parse import quote

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
            "Content-Disposition": _content_disposition(
                record.file_name, sha256
            ),
            # Never cached: the next request must verify the file again.
            "Cache-Control": "no-store",
        },
    )


def _content_disposition(file_name: str, sha256: str) -> str:
    """The save-as name, in the two forms RFC 6266 asks for.

    Header values go out as latin-1, so a model called ``别墅.3dm`` — or any
    name with an accent in it — cannot travel as itself. RFC 6266 answers this
    exactly: an ASCII ``filename`` every client can read, and a
    percent-encoded UTF-8 ``filename*`` that carries the real name for those
    that can. The ASCII form also drops the quote and backslash a receipt on
    disk would otherwise use to write this response's headers.
    """

    name = file_name or f"{sha256[:8]}.3dm"
    fallback = "".join(
        character
        if character.isascii()
        and character.isprintable()
        and character not in '"\\'
        else "_"
        for character in name
    )
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
