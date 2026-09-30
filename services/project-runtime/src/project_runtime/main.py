"""The Studio API application: settings in, FastAPI app out.

``create_app`` may read explicitly configured external actor credentials but
binds no project; the request needing it discovers a wrong project root.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
from types import MappingProxyType
from typing import AsyncIterator, Mapping, TextIO
import weakref

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

from archflow.project.index import IndexCommit, add_commit_listener
from monkeyarch.authoring.frame import FrameError
from monkeyarch.domain.massing_transforms import MassingTransformError
from monkeydiagram.documentation.sheet_layout import SheetLayoutError
from monkeydiagram.study import StudyEvidenceError

from .api import routes
from .api.routes import memory as memory_routes
from .api.routes import projections as projection_routes
from .api.routes import skills as skill_routes
from .authentication import ActorAuthorizationMiddleware, read_actor_credentials, request_action
from .application.clarification import PendingIntentStore
from .application.episodes import EpisodeStore
from .events import StudioEvents
from .application.intent_agent import compiler_from_settings
from .jobs import JobRegistry
from .application.rendering import RenderJobRecords
from .application.monitored_compiler import MonitoredCompiler
from .monitoring import StudioMonitor
from .application.options import OptionStore
from .application.proposals import ProposalStore
from .application.validation import ValidationStore
from .protocol import SERVER_VERSION
from .settings import BIND_ENV, PROJECT_DIR_ENV, REMOTE_MODE, SHARED_PROJECT_ROLE, StudioSettings
from .api.conditional import CONDITIONAL_READS, ConditionalReads
from .errors import StudioError, error_sentence

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


# The refusals the owner packages raise (#519). Each names its own wire code and
# says its own sentence; the status it answers with is this server's, and this
# table is the one place that says it. A route lets them through, and they
# arrive in the same ``{code, detail}`` body as a ``StudioError``.
OWNER_REFUSALS: Mapping[type[Exception], int] = MappingProxyType({
    FrameError: 422,
    MassingTransformError: 422,
    SheetLayoutError: 422,
    StudyEvidenceError: 422,
})


async def _handle_owner_refusal(request: Request, exc: Exception) -> JSONResponse:
    status = next(OWNER_REFUSALS[kind] for kind in type(exc).__mro__ if kind in OWNER_REFUSALS)
    return _error(status, exc.code, error_sentence(exc))


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # Starlette raises these before any route code runs: an unknown path
    # (404 NOT_FOUND) and a known path with the wrong method (405
    # METHOD_NOT_ALLOWED). ``HTTP_ERROR`` is the fallback for any other status
    # the framework itself raises — a malformed ``Range`` header, say. Route
    # code never reaches it: routes raise ``StudioError`` or let an owner's
    # refusal through, and each has its own handler and its own named code.
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


class IndexRevisionHeader:
    """A write's answer names the index revision that holds it: ``X-Monkey-Index: <epoch>:<revision>`` (#366).

    A client store that has not reached that revision yet reads the changes
    before it calls the write done. The header waits briefly for the index to
    apply the write (``ProjectBinding.index_reader``) and is left out whenever
    no index answers: the client then has nothing to wait for. Reads, refused
    writes and the event routes pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, state) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS")
                or not scope.get("path", "").startswith(_API_PREFIX) or scope.get("path", "").startswith("/api/events")):
            await self.app(scope, receive, send)
            return

        async def stamped(message) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                written = await run_in_threadpool(self._written)
                if written is not None:
                    message = {**message, "headers": [*message.get("headers", ()),
                                                       (b"x-monkey-index", written.encode("latin-1"))]}
            await send(message)

        await self.app(scope, receive, stamped)

    def _written(self) -> str | None:
        binding = getattr(self.state, "binding", None)
        if binding is None or binding.index_reader() is None:
            return None
        state = binding.index_state()
        return None if state is None else f"{state.token.epoch}:{state.token.revision}"


# The views a workspace reads first, as it asks for them (``GET /api/design-history``
# defaults to ``main``); ``_prepare_first_reads`` derives them once at start (#449).
_FIRST_READS = (("/api/design-history", b"branchId=main"), ("/api/worktrees", b""))
_PREPARING = "monkey.first_reads"


class FirstReads:
    """While ``_prepare_first_reads`` runs, a view derived from the runs waits for its part (#449).

    Those are the conditional reads (``CONDITIONAL_READS``): each would walk
    the runs beside the preparation. A prepared view (``_FIRST_READS``) waits
    until it is derived and then answers from the conditional memo under the
    project's read token, or is derived again when the project moved
    meanwhile; the others wait only for the runs. Every other request passes,
    and so does everything once preparation ended or was never asked for.
    """

    def __init__(self, app: ASGIApp, *, state) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        prepared = (getattr(self.state, "first_reads", {}).get(scope.get("path"))
                    if scope["type"] == "http" and scope.get("method") == "GET" and not scope.get(_PREPARING) else None)
        if prepared is not None and not prepared.is_set():
            await run_in_threadpool(prepared.wait, _FIRST_READS_WAIT_S)
        await self.app(scope, receive, send)


# Past this, a first read derives its own view rather than wait on a stuck preparation.
_FIRST_READS_WAIT_S = 30.0


def _publish_commits(settings: StudioSettings, events: StudioEvents):
    """Put each commit of this project's index on the event stream as ``index.committed``: a hint, no rows."""

    sink = weakref.ref(events)
    removal: list = []

    def publish(commit: IndexCommit) -> None:
        target = sink()
        if target is None:
            # The application is gone without its lifespan having ended (a test, a schema dump).
            for remove in removal:
                remove()
            return
        target.publish(event={"type": "index.committed", "epoch": commit.token.epoch,
                              "revision": commit.token.revision, "domains": sorted(commit.domains)})

    removal.append(add_commit_listener(Path(settings.project_dir).resolve(strict=False), publish))
    return removal[0]


_LOG = logging.getLogger(__name__)


def _project_commits(settings: StudioSettings, state):
    """After each commit of this project's index, queue the design tree's thumbnails that are not drawn (#367).

    The listener returns at once: it only wakes the projection worker, which
    reads the index itself. The first commit opens the queue (on a thread of
    its own), so a new state's thumbnail is drawn with nobody asking for it.
    A commit of the projection queue's own (``projections``) queues nothing.
    """

    sink = weakref.ref(state)
    removal: list = []
    opening = threading.Lock()

    def open_queue(target) -> None:
        try:
            projection_routes.projections_of(target).start()  # its first pass is this commit's
        except Exception:  # noqa: BLE001 - no index, or shutting down: the next request tries again
            _LOG.debug("the projection queue did not open on an index commit", exc_info=True)
        finally:
            opening.release()

    def listen(commit: IndexCommit) -> None:
        target = sink()
        if target is None:
            for remove in removal:
                remove()
            return
        if commit.domains <= {"projections"}:
            return
        projections = getattr(target, "projections", None)
        if projections is not None:
            projections.committed()
        elif getattr(target, "binding", None) is not None and opening.acquire(blocking=False):
            threading.Thread(target=open_queue, args=(target,), daemon=True, name="projection-open").start()

    removal.append(add_commit_listener(Path(settings.project_dir).resolve(strict=False), listen))
    return removal[0]


def _authorization(scope: Scope) -> str:
    """The request's ``Authorization`` header, decoded, or the empty string."""

    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            return value.decode("latin-1")
    return ""


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare native drawing libraries and follow index commits; on shutdown drain accepted work and close the binding.

    From startup on, every commit of the project index queues the design
    tree's thumbnails that are not drawn yet (``_project_commits``).

    A candidate run writes P036 records; killing its thread mid-run would
    leave a run directory nobody can account for. Shutting the worker down and
    waiting is the difference between a service that stops and one that stops
    cleanly. Closing the binding stops its project index's keeper - what it had
    not applied yet, the next open reconciles from the project's layout - and
    its layout watch, which holds the project folder open.
    """

    if app.state.settings.service_role != SHARED_PROJECT_ROLE:
        # Initialize NumPy/GEOS on the server thread before sync request workers
        # can compete with their first Windows DLL load during sheet outlining.
        from monkeydiagram.documentation.styles import initialize_drawing_runtime

        initialize_drawing_runtime()
        app.state.stop_projection_commits = _project_commits(app.state.settings, app.state)
    if getattr(app.state, "prepare_first_reads", False) and app.state.settings.service_role != SHARED_PROJECT_ROLE:
        app.state.first_reads = {path: threading.Event() for path in CONDITIONAL_READS}
        threading.Thread(target=_prepare_first_reads, args=(app,), name="studio-first-reads", daemon=True).start()

    yield
    app.state.stop_index_events()
    app.state.jobs.stop_accepting()
    app.state.render_jobs.stop_accepting()
    app.state.stop_projection_commits()
    with app.state.projections_lock:
        app.state.projections_closed = True
        projections, app.state.projections = app.state.projections, None
    if projections is not None:
        await run_in_threadpool(projections.shutdown)
    await run_in_threadpool(app.state.render_jobs.shutdown)
    await run_in_threadpool(app.state.jobs.shutdown)
    binding = getattr(app.state, "binding", None)
    if binding is not None:
        # Its layout watch holds a handle on the project folder, and its index
        # keeper the index file and its lock: let go of both with the project,
        # not whenever the process happens to exit.
        await run_in_threadpool(binding.close)


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


def create_app(settings: StudioSettings, *, render_adapter=None) -> FastAPI:
    credentials = read_actor_credentials(settings.actors_file, settings.project_dir) if settings.actors_file is not None else None
    shared_project = settings.service_role == SHARED_PROJECT_ROLE
    app = FastAPI(
        title="ArchFlow Studio API", version=SERVER_VERSION, lifespan=_lifespan,
        dependencies=[Depends(_diagnostic_request)],
    )
    app.state.settings = settings
    from monkeymonitor.store import UsageLog

    app.state.monitor = StudioMonitor(UsageLog(settings.monitor_dir) if settings.monitor_dir is not None else None)
    if render_adapter is None and settings.render_provider != "off" and not shared_project:
        from .render_adapters.gemini import adapter_from_settings

        render_adapter = adapter_from_settings(settings)
    app.state.render_jobs = RenderJobRecords(None if shared_project else render_adapter, monitor=app.state.monitor)
    if shared_project:
        app.state.render_jobs.stop_accepting()
    # The projection queue (ADR-008): content-keyed pictures of retained models
    # in the project's Hub cache, drawn by one background worker. Opened with
    # the binding on the first projection request; never for a shared project.
    app.state.projections = None
    app.state.projections_lock = threading.Lock()
    # Proposals live in this process and nowhere else. The store is created
    # here so that fact is visible at the top of the application rather than
    # accumulating quietly at the bottom of a route.
    app.state.proposals = ProposalStore()
    # The event sink is the reserved ``StudioEventSink`` port, and the job
    # registry owns the one worker thread candidates run on. Both are created
    # here for the same reason as the store: what this process holds in memory,
    # and therefore loses on restart, is stated at the top of the application.
    app.state.events = StudioEvents()
    # The project index's commits reach clients as ``index.committed`` on the same stream.
    app.state.stop_index_events = _publish_commits(settings, app.state.events)
    # And, once the application has started, they queue the design tree's thumbnails (#367).
    app.state.stop_projection_commits = lambda: None
    app.state.jobs = JobRegistry(app.state.events, max_workers=settings.workers, monitor=app.state.monitor)
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
    for refusal in OWNER_REFUSALS:
        app.add_exception_handler(refusal, _handle_owner_refusal)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(routes.router)
    # Project memory is its own owner (studio.memory, ADR-009), beside decisions.
    app.include_router(memory_routes.router, prefix=_API_PREFIX)
    if not shared_project:
        app.include_router(projection_routes.router, prefix=_API_PREFIX)
        # The library project's skills (#252), read by the Hub for its agents.
        app.include_router(skill_routes.router, prefix=_API_PREFIX)
    # Added first, so it sits inside the token and CORS middlewares below: a
    # request is authenticated before a remembered answer can be handed out.
    app.add_middleware(ConditionalReads, state=app.state)
    app.add_middleware(IndexRevisionHeader, state=app.state)
    app.add_middleware(FirstReads, state=app.state)
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


def _watch_managed_stdin(server: uvicorn.Server, jobs: JobRegistry, stream: TextIO, render_jobs=None) -> None:
    """Only the owning parent's pipe requests a managed shutdown."""

    for line in stream:
        if line.strip() == "stop":
            break
    jobs.stop_accepting()
    if render_jobs is not None:
        render_jobs.stop_accepting()
    server.should_exit = True


def _stop_streams_on_signal(server: uvicorn.Server, state) -> None:
    """A signalled stop also ends the event streams, as the owner's ``stop`` does.

    An open ``/api/events`` stream ends once jobs stop accepting, and uvicorn
    waits for open connections before it runs the shutdown that stops them: a
    worker told to exit while its Hub followed its events never exited.
    """

    handle_exit = server.handle_exit

    def stop(sig, frame) -> None:
        # Off the signal handler: stopping takes the registries' locks.
        threading.Thread(target=lambda: (state.jobs.stop_accepting(), state.render_jobs.stop_accepting()),
                         name="studio-signalled-stop", daemon=True).start()
        handle_exit(sig, frame)

    server.handle_exit = stop


def _prepare_first_reads(app: FastAPI) -> None:
    """What every first request would otherwise build for itself, built once while the process is idle (#449).

    FastAPI builds each included router's route state on the first request
    routed through it, and the workspace's first reads each list every run's
    records (``prepare_bound_project``), with or without a project index: the
    design history and the worktrees read the runs themselves. Both are done
    here, on a thread of their own, once the process serves; a path no route
    has walks every router. Then the first views (``_FIRST_READS``) are derived
    through the application, so the conditional memo keeps each one under the
    project's read token: only a stable token keeps an answer, and a token the
    project moved past never answers it again. With an index, the worktrees
    are derived again after its first load, whose commit is one of the events
    their tag names; nobody waits for that load. A first request arriving
    meanwhile waits for the one build instead of walking the runs beside it
    (``FirstReads``). Nothing is recorded. ``main`` asks for it; an
    application a test builds binds on its first request as before.
    """

    settings = app.state.settings
    unmatched = {"type": "http", "method": "GET", "path": "/api/\0", "raw_path": b"/api/%00",
                 "root_path": "", "query_string": b"", "headers": [], "app": app}
    try:
        from .binding import prepare_bound_project

        binding = prepare_bound_project(app.state)
        for route in app.router.routes:
            route.matches(dict(unmatched))
        prepared = dict(_FIRST_READS) if settings.mode != REMOTE_MODE else {}
        # A remote process answers only a caller with its token; it derives its views when asked.
        for path, done in app.state.first_reads.items():
            if path not in prepared:
                done.set()
        # The worktrees' tag names the event sequence, which the index's first
        # commit moves: derived again then, with nobody waiting for it.
        sequence = app.state.events.sequence
        for path, query in prepared.items():
            asyncio.run(_read_through(app, path, query))
            app.state.first_reads[path].set()
        if prepared and binding.await_index(_FIRST_READS_WAIT_S) is not None and app.state.events.sequence != sequence:
            asyncio.run(_read_through(app, "/api/worktrees", prepared["/api/worktrees"]))
    except Exception:  # noqa: BLE001 - the first request opens and refuses for itself
        logging.getLogger(__name__).debug("first-read preparation did not finish", exc_info=True)
    finally:
        for done in app.state.first_reads.values():
            done.set()


async def _read_through(app: FastAPI, path: str, query: bytes) -> None:
    """One GET through the whole application, the answer discarded: the memo keeps it."""

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode("latin-1"), "root_path": "",
             "query_string": query, "headers": [], "client": None, "server": None, _PREPARING: True}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        pass

    await app(scope, receive, send)


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
    app.state.prepare_first_reads = True
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
    _stop_streams_on_signal(server, app.state)
    threading.Thread(
        target=_watch_managed_stdin, args=(server, app.state.jobs, sys.stdin, app.state.render_jobs),
        name="studio-owner-input", daemon=True,
    ).start()
    try:
        server.run()
    finally:
        app.state.jobs.shutdown()


if __name__ == "__main__":
    main()
