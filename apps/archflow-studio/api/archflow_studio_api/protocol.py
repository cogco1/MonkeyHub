"""What this server *is*, on the wire: the open ArchFlow protocol, version 1.

The Studio has been one program in two halves so far, and the halves knew each
other by having been started together. A remote server and a second client
cannot know each other that way, so the boundary between them gets a name, a
version and one route that states both. ``docs/PROTOCOL.md`` is the written
form of what this module names.

Three constants and one function, and nothing else belongs here:

* ``PROTOCOL`` — the protocol identity, ``archflow/<major>``. A client refuses
  a server whose major differs from the one it was built against; a minor
  version adds resources and fields and never removes or renames them.
* ``SERVER_NAME`` — which implementation is answering. ``monkeyarch-api`` is
  this one; the protocol is open, so it is not the only name a conforming
  server may send.
* ``SERVER_VERSION`` — the one version constant of this server. The FastAPI
  application is built with it, so the OpenAPI document and the protocol
  answer can never disagree about which build is running.

``server_capabilities`` is the honest half. A capability names a feature this
process actually serves *now*, so a client can hide what a server cannot do
instead of discovering it as a 404. It is computed from settings rather than
written down, which is why ``rhino-export`` appears only where an export can
really happen.
"""

from __future__ import annotations

from .settings import StudioSettings

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
PROTOCOL = f"archflow/{PROTOCOL_MAJOR}"

SERVER_NAME = "monkeyarch-api"
SERVER_VERSION = "0.1.0"

# The features every build of this server serves, whatever it is configured
# with. Sorted, because a capability list that reordered itself between two
# reads would look like a server that had changed.
BASE_CAPABILITIES: tuple[str, ...] = (
    "artifacts",
    "candidates",
    "compare",
    "events",
    "gestures",
    "intents",
    "pick",
    "program",
    "projection",
    "proposals",
    "validation",
)

# The one feature that depends on this machine rather than on this build.
RHINO_EXPORT_CAPABILITY = "rhino-export"


def server_capabilities(settings: StudioSettings) -> tuple[str, ...]:
    """The feature names this process serves, in a stable order.

    ``/api/health``, ``/api/protocol`` and ``/api/projects`` are not in the
    list: they are the handshake itself, and a conforming server always has
    them. Everything here is a design feature a client may choose to offer or
    to hide.
    """

    capabilities = list(BASE_CAPABILITIES)
    if settings.rhino_export:
        capabilities.append(RHINO_EXPORT_CAPABILITY)
    return tuple(sorted(capabilities))
