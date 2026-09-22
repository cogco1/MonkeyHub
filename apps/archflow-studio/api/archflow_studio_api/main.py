"""The Studio API application: settings in, FastAPI app out.

``create_app`` may read explicitly configured external actor credentials but
binds no project; the request needing it discovers a wrong project root.
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
from typing import AsyncIterator, TextIO

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

from . import routes
from .application.authentication import ActorAuthorizationMiddleware, read_actor_credentials, request_action
from .application.clarification import PendingIntentStore
from .application.episodes import EpisodeStore
from .application.events import StudioEvents
from .application.intent_agent import compiler_from_settings
from .application.jobs import JobRegistry
from .application.monitoring import MonitoredCompiler, StudioMonitor
from .application.options import OptionStore
from .application.proposals import ProposalStore
from .application.validation import ValidationStore
from .protocol import SERVER_VERSION
from .settings import BIND_ENV, PROJECT_DIR_ENV, REMOTE_MODE, SHARED_PROJECT_ROLE, StudioSettings
from .transport.errors import StudioError

DEFAULT_PORT = 8000
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}

# The prefix every protocol resource lives under, and the two routes inside it
# that answer before a client has been asked for anything. A client that could
# not read the handshake could not learn that it needs a token, and a liveness
# probe is not a client.
_API_PREFIX = "/api"
_OPEN_PATHS = frozenset({"/api/health", "/api/protocol"})
_BEARER = "Bearer "


def _error(status: int, code: str, detail: str, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "detail": detail},
        headers=headers,
    )


async def _handle_studio_error(request: Request, exc: StudioError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # Starlette raises these before any route code runs: an unknown path
    # (404 NOT_FOUND) and a known path with the wrong method (405
    # METHOD_NOT_ALLOWED). ``HTTP_ERROR`` is the fallback for any other status
    # the framework itself raises — a malformed ``Range`` header, say. Route
    # code never reaches it: routes raise ``StudioError``, which has its own
    # handler and its own named code.
    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return _error(exc.status_code, code, str(exc.detail), getattr(exc, "headers", None))


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ()))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return _error(422, "REQUEST_INVALID", "; ".join(parts) or "the request could not be read")


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug answers in the same shape and says nothing about itself."""

    return _error(
        500,
        "INTERNAL_ERROR",
        "The API failed while handling this request; see the service log.",
    )


class BearerTokenMiddleware:
    """In remote mode, every ``/api`` route but health and protocol needs the token.

    Written as a plain ASGI middleware rather than a route dependency for two
    reasons. It runs before routing, so an unknown path on an authenticated
    server answers 401 rather than telling an anonymous caller which paths
    exist; and it leaves the event stream alone, which a request/response
    middleware would sit in the middle of for the life of the connection.

    ``OPTIONS`` passes through: a browser's CORS preflight carries no
    ``Authorization`` header by definition, and refusing it would refuse the
    request that follows it.
    """

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        response = _error(
            401,
            "UNAUTHENTICATED",
            "this server runs in remote mode and answers only a request "
            "carrying Authorization: Bearer <token>. GET /api/protocol says "
            "which server this is and that it is in remote mode; it and "
            "GET /api/health are the two routes that need no token.",
            {"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _allowed(self, scope: Scope) -> bool:
        path = scope.get("path", "")
        if not path.startswith(_API_PREFIX) or path in _OPEN_PATHS:
            return True
        if scope.get("method") == "OPTIONS":
            return True
        header = _authorization(scope)
        if not header.startswith(_BEARER):
            return False
        # Constant time: a token compared with == leaks its own prefix to
        # anyone who can measure how long the refusal took.
        return secrets.compare_digest(header[len(_BEARER):].strip(), self.token)


def _authorization(scope: Scope) -> str:
    """The request's ``Authorization`` header, decoded, or the empty string."""

    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            return value.decode("latin-1")
    return ""


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare native drawing libraries and drain accepted work on shutdown.

    A candidate run writes P036 records; killing its thread mid-run would
    leave a run directory nobody can account for. Shutting the worker down and
    waiting is the difference between a service that stops and one that stops
    cleanly.
    """

    if app.state.settings.service_role != SHARED_PROJECT_ROLE:
        # Initialize NumPy/GEOS on the server thread before sync request workers
        # can compete with their first Windows DLL load during sheet outlining.
        from monkeydiagram.documentation.styles import initialize_drawing_runtime

        initialize_drawing_runtime()

    yield
    app.state.jobs.shutdown()
    app.state.native_render_jobs.shutdown()


async def _diagnostic_request(request: Request,
    x_monkey_operation: str | None = Header(default=None),
    x_monkey_parent: str | None = Header(default=None),
):
    """Carry one browser operation through sync route workers without global state."""
    from uuid import UUID

    monitor = request.app.state.monitor
    turn_header = request.headers.get("x-monkey-turn-id")
    if monitor.store is None or (x_monkey_operation is None and turn_header is None) or request.url.path.startswith("/api/events"):
        yield
        return
    try:
        turn_id = str(UUID(turn_header)) if turn_header else None
        operation_id = f"studio:client:{UUID(x_monkey_operation)}" if x_monkey_operation else None
        parent_id = f"studio:client:{UUID(x_monkey_parent)}" if x_monkey_parent else operation_id or f"hub:turn:{turn_id}"
        hub_parent = request.headers.get("x-monkey-parent-span-id")
        if turn_id and hub_parent and len(hub_parent) <= 256 and all(char.isalnum() or char in ":._-" for char in hub_parent):
            parent_id = hub_parent
    except ValueError:
        # A malformed optional diagnostic header does not refuse project work.
        yield
        return
    with monitor.scope(operation_id=operation_id, parent_event_id=parent_id, turn_id=turn_id):
        route = request.scope.get("route")
        with monitor.measure("api_request", details={"request_kind": f"{request.method} {getattr(route, 'path', request.url.path)}"}) as interval:
            try:
                yield
            finally:
                # The route may bind the project on first use. Observing it must
                # never open a project just to populate a diagnostic record.
                binding = getattr(request.app.state, "binding", None)
                if binding is not None:
                    interval["project_id"] = binding.project_id


def create_app(settings: StudioSettings) -> FastAPI:
    credentials = read_actor_credentials(settings.actors_file, settings.project_dir) if settings.actors_file is not None else None
    shared_project = settings.service_role == SHARED_PROJECT_ROLE
    app = FastAPI(
        title="ArchFlow Studio API", version=SERVER_VERSION, lifespan=_lifespan,
        dependencies=[Depends(_diagnostic_request)],
    )
    app.state.settings = settings
    from monkeymonitor.store import UsageLog

    app.state.monitor = StudioMonitor(UsageLog(settings.monitor_dir) if settings.monitor_dir is not None else None)
    # Proposals live in this process and nowhere else. The store is created
    # here so that fact is visible at the top of the application rather than
    # accumulating quietly at the bottom of a route.
    app.state.proposals = ProposalStore()
    # The event sink is the reserved ``StudioEventSink`` port, and the job
    # registry owns the one worker thread candidates run on. Both are created
    # here for the same reason as the store: what this process holds in memory,
    # and therefore loses on restart, is stated at the top of the application.
    app.state.events = StudioEvents()
    app.state.jobs = JobRegistry(app.state.events, max_workers=settings.workers, monitor=app.state.monitor)
    from .application.rendering import NativeRenderJobs
    app.state.native_render_jobs = NativeRenderJobs()
    if shared_project:
        app.state.jobs.stop_accepting()
    # One validation per candidate, remembered so that reading a verdict twice
    # is one verdict and one event rather than two of each. In memory, like
    # everything above it, and lost on restart for the same reason.
    app.state.validations = ValidationStore()
    # One pending clarification per exchange, keyed by its continuation token
    # and bound to the stateDigest it was opened against. The chat log is not
    # the truth about what was asked; this is, and like everything above it,
    # it is one process's memory and is lost on restart.
    app.state.pending_intents = PendingIntentStore()
    # The massing options on the table. The option itself — its transform, its
    # metrics, the state it was measured against — is this process's memory
    # like the proposals above it; the pack each one carries is retained in a
    # run of its own, so the shapes outlive the restart and the table does not.
    app.state.options = OptionStore()
    # The judgements this process has made: which proposal was accepted,
    # rejected or modified, and why. Unlike everything above it, this one does
    # not stay in memory — a judgement is written into the candidate run it
    # produced, and the store holds only the ones that have not met a run yet.
    # Those are lost on restart, and every episode says which of the two it is.
    app.state.episodes = EpisodeStore()
    # Who compiles an architect's sentence into the grammar: nobody (the
    # deterministic pass-through), a local codex process, or the Anthropic
    # API — chosen by the settings this app was built with, held here so a
    # test can put a scripted compiler in its place and the route stays one
    # code path. A provider that cannot name its own version refuses here,
    # before a request arrives, rather than at the first sentence.
    app.state.intent_compiler = None if shared_project else compiler_from_settings(settings)
    if settings.monitor_dir is not None and not shared_project:
        app.state.intent_compiler = MonitoredCompiler(
            app.state.intent_compiler, app.state.monitor
        )
    app.add_exception_handler(StudioError, _handle_studio_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(routes.router)
    if shared_project:
        original_openapi = app.openapi

        def shared_openapi():
            schema = original_openapi()
            schema["paths"] = {
                path: permitted for path, operations in schema["paths"].items()
                if (permitted := {
                    method: value for method, value in operations.items()
                    if request_action(method.upper(), path, shared_project=True) is not None
                })
            }
            return schema

        app.openapi = shared_openapi
    # Remote mode, and only remote mode, adds the two middlewares below.
    # ``StudioSettings`` has already refused a remote process with no token and
    # no origins, so there is nothing left to check here. CORS is added last
    # and therefore sits outermost, which is what lets a browser read the 401
    # the token gate answers with instead of a bare network failure.
    if settings.mode == REMOTE_MODE:
        if credentials is not None:
            app.add_middleware(ActorAuthorizationMiddleware, credentials=credentials, service_role=settings.service_role)
        else:
            assert settings.api_token is not None
            app.add_middleware(BearerTokenMiddleware, token=settings.api_token)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Monkey-Operation", "X-Monkey-Parent"],
            # The two headers the artifact-bytes route answers with that a
            # browser cannot read unless they are named here.
            expose_headers=["ETag", "Content-Disposition"],
        )
    return app


def _source_revision(root: Path = REPOSITORY_ROOT) -> str | None:
    """Use the packaged source identity, or the checkout that contains this code."""

    packaged = root / "source-version.txt"
    if packaged.is_file():
        revision = packaged.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            raise ValueError("source-version.txt must contain one full source commit SHA")
        return revision
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) else None


def _watch_managed_stdin(server: uvicorn.Server, jobs: JobRegistry, stream: TextIO) -> None:
    """Only the owning parent's pipe requests a managed shutdown."""

    for line in stream:
        if line.strip() == "stop":
            break
    jobs.stop_accepting()
    server.should_exit = True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="archflow-studio-api", description="Serve the ArchFlow Studio API."
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Interface to bind; overrides ARCHFLOW_STUDIO_BIND. Left out, "
        "the settings decide, and they default to loopback.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--managed-stdin", action="store_true", help="Stop gracefully on stdin stop or EOF.")
    parser.add_argument("--managed-instance-id", default=None, help="The owning Hub's unique launch identifier.")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help=f"Project root to bind; overrides {PROJECT_DIR_ENV}.",
    )
    args = parser.parse_args(argv)
    if args.managed_stdin != bool(args.managed_instance_id):
        parser.error("--managed-stdin and --managed-instance-id must be supplied together")
    if args.project_dir is not None:
        os.environ[PROJECT_DIR_ENV] = str(args.project_dir)
    if args.host is not None:
        os.environ[BIND_ENV] = args.host
    settings = StudioSettings.from_env()
    app = create_app(settings)
    app.state.server_version = SERVER_VERSION
    app.state.process_id = os.getpid()
    app.state.parent_process_id = os.getppid()
    app.state.source_revision = _source_revision()
    app.state.managed_instance_id = args.managed_instance_id
    if not args.managed_stdin:
        uvicorn.run(app, host=settings.bind_host, port=args.port)
        return
    if settings.service_role != SHARED_PROJECT_ROLE:
        # A blocking stdin reader can also stall NumPy's first Windows DLL load.
        # Managed launches must initialize before starting that reader, while
        # lifespan still prepares ordinary ASGI and standalone launches.
        from monkeydiagram.documentation.styles import initialize_drawing_runtime

        initialize_drawing_runtime()
    server = uvicorn.Server(uvicorn.Config(app, host=settings.bind_host, port=args.port))
    threading.Thread(
        target=_watch_managed_stdin, args=(server, app.state.jobs, sys.stdin),
        name="studio-owner-input", daemon=True,
    ).start()
    try:
        server.run()
    finally:
        app.state.jobs.shutdown()


if __name__ == "__main__":
    main()
