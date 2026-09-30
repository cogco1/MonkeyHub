"""``GET /api/protocol``: the one route a client asks before anything else.

It opens no project and reads no design. It is answerable by a server whose
project directory is wrong, which is the point: a client must be able to tell
"this is not a server I speak to" apart from "this server cannot find its
project". Like ``/api/health``, it is served without a token even in remote
mode — a client that could not read the handshake could not learn that it
needs one.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from ..transport.protocol import ServerIdentityDto, server_identity_dto

router = APIRouter(tags=["protocol"])


@router.get(
    "/protocol",
    response_model=ServerIdentityDto,
    response_model_by_alias=True,
)
def read_protocol(request: Request) -> ServerIdentityDto:
    """Name the protocol, the server, its version, its mode and what it does."""

    return server_identity_dto(request.app.state.settings)
