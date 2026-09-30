"""The handshake answer: which protocol, which server, which mode, what it does."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..protocol import PROTOCOL, SERVER_NAME, SERVER_VERSION, server_capabilities
from ..settings import StudioSettings


class ServerIdentityDto(BaseModel):
    """The wire form of ``GET /api/protocol``.

    Four facts and a list. The first three are what a client needs before it
    trusts anything else this server says; ``mode`` is why a request may be
    refused with ``UNAUTHENTICATED``; ``capabilities`` is what the client may
    ask for without discovering the answer as a 404.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    protocol: str = Field(
        description="the protocol and its major version, 'archflow/2'"
    )
    server: str = Field(
        description="which implementation is answering, e.g. monkeyarch-api"
    )
    server_version: str = Field(
        alias="serverVersion",
        description="the version of that implementation, not of the protocol",
    )
    mode: str = Field(
        description="local (unauthenticated, one machine) or remote (every "
        "route but health and protocol requires a bearer token)",
    )
    capabilities: list[str] = Field(
        description="the feature names this process actually serves now",
    )


def server_identity_dto(settings: StudioSettings) -> ServerIdentityDto:
    """State the boundary this process serves, from its own constants."""

    return ServerIdentityDto(
        protocol=PROTOCOL,
        server=SERVER_NAME,
        server_version=SERVER_VERSION,
        mode=settings.mode,
        capabilities=list(server_capabilities(settings)),
    )
