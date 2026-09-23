"""Operator-configured actors and the actions exposed by this API process."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..settings import SettingsError, SHARED_PROJECT_ROLE
from ..transport.errors import StudioError
from .binding import bound_project

_ACTIONS = frozenset({"read", "propose", "accept", "release"})
_OPEN_PATHS = frozenset({"/api/health", "/api/protocol"})
_ACCEPT_PATHS = (
    r"/api/design-stages/initialize",
    r"/api/design-branches",
    r"/api/candidates/[^/]+/accept",
    # A scoped decision is an explicit judgement of the studio: it uses the
    # existing decision grant and still accepts no Stage and moves no HEAD.
    r"/api/decisions",
    r"/api/decisions/[^/]+/revisions",
)
_SHARED_READ_PATHS = (
    r"/api/(?:health|protocol|project|design-history|artifacts|documents|events)",
    r"/api/projects(?:/[^/]+)?",
    r"/api/state(?:/frame|/volumes)?",
    r"/api/artifacts/[^/]+/bytes",
    r"/api/documents/[^/]+/bytes",
    r"/api/candidates/[^/]+(?:/validation|/compare)?",
    r"/api/working-copies(?:/[^/]+)?",
    r"/api/episodes(?:/[^/]+)?",
    r"/api/sync/(?:manifest|files)",
    r"/api/decisions(?:/[^/]+)?",
)


# The surfaces this boundary can name as an origin. Both are facts about how
# this process was started - a Studio the Hub manages carries the instance id
# it was launched with - and never a request header, which any caller can set.
ORIGIN_STUDIO = "studio"
ORIGIN_HUB = "hub"

# What an explicit action is attributed to where the process runs with no actor
# credentials configured at all: the local unauthenticated boundary itself,
# stated as such rather than borrowed from a person who was never identified.
LOCAL_ACTOR_ID = "studio:explicit-user-action"


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    actor_id: str
    project_actions: tuple[tuple[str, frozenset[str]], ...]

    def allows(self, project_id: str, action: str) -> bool:
        return any(project == project_id and action in actions for project, actions in self.project_actions)


@dataclass(frozen=True, slots=True)
class ActorCredentials:
    """Loaded only from service configuration; never attached to a request."""

    entries: tuple[tuple[bytes, AuthenticatedActor], ...] = field(repr=False)

    def authenticate(self, header: str) -> AuthenticatedActor | None:
        if not header.startswith("Bearer "):
            return None
        supplied = header[len("Bearer "):].strip().encode("utf-8")
        for token, actor in self.entries:
            if secrets.compare_digest(token, supplied):
                return actor
        return None


def read_actor_credentials(path: Path, project_dir: Path) -> ActorCredentials:
    """Read one external config, with errors that never reproduce credentials."""

    if path.resolve().is_relative_to(project_dir.resolve()):
        raise SettingsError("Actor credentials must be configured outside the project directory.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["actors"]
        if not isinstance(rows, list) or not rows:
            raise ValueError
        actors: set[str] = set()
        tokens: set[bytes] = set()
        entries = []
        for row in rows:
            actor_id, token, projects = row["actor_id"], row["token"], row["projects"]
            if not isinstance(actor_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", actor_id):
                raise ValueError
            if not isinstance(token, str) or not token.strip() or token != token.strip():
                raise ValueError
            secret = token.encode("utf-8")
            if actor_id in actors or secret in tokens or not isinstance(projects, dict) or not projects:
                raise ValueError
            scopes = []
            for project_id, actions in projects.items():
                if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project_id):
                    raise ValueError
                if not isinstance(actions, list) or not actions or any(not isinstance(action, str) or action not in _ACTIONS for action in actions):
                    raise ValueError
                scopes.append((project_id, frozenset(actions)))
            entries.append((secret, AuthenticatedActor(actor_id, tuple(scopes))))
            actors.add(actor_id)
            tokens.add(secret)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SettingsError("Actor configuration could not be read: expected actors with unique actor_id/token and explicit project actions.") from None
    return ActorCredentials(tuple(entries))


def authenticated_actor(request: Request) -> AuthenticatedActor | None:
    """The authenticated caller, independent of every request-body field."""

    return getattr(request.state, "actor", None)


@dataclass(frozen=True, slots=True)
class ActorAttribution:
    """Who caused one request, and through which surface, as this boundary knows it.

    ``actor_id`` is the authenticated actor's own id, or the local boundary's
    explicit identity where the process was configured with no credentials;
    ``authenticated`` says which of the two it is, so nothing retained later
    has to guess whether a name was proven. Every field is read from the
    middleware's resolved actor and this process's own settings: no request
    body and no request header takes part, because both are the caller's.
    """

    actor_id: str
    authenticated: bool
    origin: str


def request_attribution(request: Request) -> ActorAttribution:
    """The actor and origin to bind a decision to, from the request boundary."""

    origin = (
        ORIGIN_HUB
        if getattr(request.app.state, "managed_instance_id", None)
        else ORIGIN_STUDIO
    )
    actor = authenticated_actor(request)
    if actor is None:
        return ActorAttribution(LOCAL_ACTOR_ID, False, origin)
    return ActorAttribution(actor.actor_id, True, origin)


def require_actor(request: Request, action: str, project_id: str | None = None) -> AuthenticatedActor:
    actor = authenticated_actor(request)
    if actor is None:
        raise StudioError(401, "UNAUTHENTICATED", "An authenticated actor is required.")
    binding = bound_project(request.app.state)
    if (project_id is not None and project_id != binding.project_id) or not actor.allows(binding.project_id, action):
        raise StudioError(403, "ACTION_FORBIDDEN", "The authenticated actor is not granted this action on the bound project.")
    return actor


def request_action(method: str, path: str, *, shared_project: bool) -> str | None:
    """One action mapping for both route assembly and request authorization."""

    if not shared_project and method == "POST" and path == "/api/drawings/plans/status":
        # Exact source references travel in a body, but freshness/anchor inspection
        # is a read. Shared services still exclude this local geometry computation.
        return "read"
    if not shared_project and method == "POST" and path == "/api/proposals/parameter-locks":
        # An explicit constraint decision uses the existing decision grant;
        # it still produces only a detached candidate, never Stage acceptance.
        return "accept"
    if method in {"GET", "HEAD"}:
        if not shared_project or any(re.fullmatch(pattern, path) for pattern in _SHARED_READ_PATHS):
            return "read"
    if method == "POST" and any(re.fullmatch(pattern, path) for pattern in _ACCEPT_PATHS):
        return "accept"
    if method == "POST" and path == "/api/sync/candidates":
        return "propose"
    if not shared_project and method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "propose"
    return None


class ActorAuthorizationMiddleware:
    def __init__(self, app: ASGIApp, *, credentials: ActorCredentials, service_role: str) -> None:
        self.app = app
        self.credentials = credentials
        self.shared_project = service_role == SHARED_PROJECT_ROLE

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api" or path.startswith("/api/")) or path in _OPEN_PATHS or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        actor = self.credentials.authenticate(request.headers.get("authorization", ""))
        if actor is None:
            await JSONResponse({"code": "UNAUTHENTICATED", "detail": "A configured actor bearer token is required."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        scope.setdefault("state", {})["actor"] = actor
        action = request_action(scope.get("method", ""), path, shared_project=self.shared_project)
        if action is None:
            await JSONResponse({"code": "SERVICE_ROLE_FORBIDDEN", "detail": "This service does not expose that operation."}, status_code=403)(scope, receive, send)
            return
        try:
            require_actor(request, action)
        except StudioError as exc:
            await JSONResponse(exc.body(), status_code=exc.status)(scope, receive, send)
            return
        await self.app(scope, receive, send)
