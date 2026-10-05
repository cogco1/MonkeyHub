"""The Hub's FastAPI application for one explicit nonproject runtime root.

``create_app`` composes the services the Hub owns (application cards,
conversations, project runtimes, desktop updates), the local origin boundary
every request passes and the routes each group serves, in the one order the
Hub's OpenAPI document and its generated client keep.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.requests import Request
from monkeycad.integration_packs import IntegrationPackManager

from project_runtime.errors import StudioError

from .. import project_archive
from ..chat.routes import projects_router, providers_router, sessions_router
from ..chat.store import ChatStore
from ..computer_tools import ComputerService
from ..fabrication import Fabrication
from ..runtime.applications import Applications
from ..team.service import Teams
from ..team.routes import team_router
from ..runtime.manager import ProjectRuntimeManager
from ..runtime.routes import apps_and_modeling_routers, runtime_router
from ..settings.routes import app_settings_router, credentials_router, router as preferences_router
from ..settings.store import SettingsError
from ..updates.desktop_updates import DesktopUpdates
from ..updates.routes import updates_router
from ..models import (
    FabPrepareRequest, FabPrepareResult, FabProfile, FabSendRequest, FabSendResult, HubError, HubFailure, HubHealth,
    ProjectArchiveExportRequest, ProjectArchiveRestoreRequest, ProjectArchiveRestoreResult, ProjectArchiveSummary,
    ComputerActionRequest, ComputerInspectRequest, ComputerRecordingRequest,
)

SOURCE_ROOT = Path(__file__).resolve().parents[5]


def _close_computer(app) -> None:
    """Stop the computer-use runtime under the lock that hands it out.

    Reading the attribute without it could miss a service another thread was
    still composing, and leave two PowerShell hosts running after the Hub
    thinks it has closed everything it owns.
    """
    with app.state.computer_lock:
        service, app.state.computer = app.state.computer, None
        if service is not None:
            service.close()


@dataclass(frozen=True)
class HubSettings:
    runtime_root: Path
    port: int = 8790
    hub_web_dir: Path | None = None
    web_origin: str | None = None
    managed_instance_id: str | None = None
    mode: str = "local"

    def __post_init__(self):
        if not self.runtime_root.is_absolute():
            raise ValueError("runtime_root must be an absolute nonproject directory")
        if not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        if self.web_origin:
            origin = urlsplit(self.web_origin)
            if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"} or origin.path or origin.query or origin.fragment or origin.username:
                raise ValueError("web_origin must name one local HTTP development origin")


def create_app(settings: HubSettings, *, source_root: Path = SOURCE_ROOT) -> FastAPI:
    applications = Applications(source_root, settings.runtime_root, settings.port)
    teams = Teams(applications)
    applications.teams = teams
    fabrication = Fabrication(source_root)
    chats = ChatStore(settings.runtime_root, f"http://127.0.0.1:{settings.port}", applications=applications)
    runtimes = ProjectRuntimeManager(applications, chats)
    chats.on_change = runtimes.chat_changed

    def update_busy() -> str | None:
        if any(row.status == "running" for row in chats.list()):
            return "Wait for the running conversations to finish before restarting."
        snapshot = runtimes.snapshot()
        for project in snapshot.projects:
            if any(row.status in {"queued", "planning", "validated", "executing", "committing"} for row in project.operations):
                return "Wait for the accepted project operations to finish before restarting."
            if project.retained and any(row.status in {"queued", "running"} for row in project.retained.jobs):
                return "Wait for the project jobs to finish before restarting."
        return None

    updates = DesktopUpdates(source_root, settings.runtime_root / "updates",
                             managed=settings.managed_instance_id is not None, busy=update_busy)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.to_thread(applications.start, "monkeymonitor")
        except (HubFailure, OSError, SettingsError, ValidationError, UnicodeError) as exc:
            # A launch failure must leave the Hub available for configuration
            # and an explicit retry through the existing application route.
            logging.getLogger(__name__).warning("MonkeyMonitor could not be prepared: %s", exc)
        # Packaged desktop only: finish this version's own next-launch
        # activation once it answers health, and check for updates.
        updates.start(settings.port, settings.managed_instance_id)
        await asyncio.to_thread(teams.restore)
        yield
        await asyncio.to_thread(teams.close)
        await asyncio.to_thread(updates.shutdown)
        await asyncio.to_thread(chats.shutdown)
        await asyncio.to_thread(applications.shutdown)
        await asyncio.to_thread(runtimes.shutdown)
        await asyncio.to_thread(_close_computer, app)

    app = FastAPI(title="MonkeyHub API", version="0.1.0", lifespan=lifespan, servers=[{"url": "/"}])
    app.state.settings = settings
    app.state.applications = applications
    app.state.teams = teams
    app.state.chats = chats
    app.state.runtimes = runtimes
    app.state.updates = updates
    app.state.integrations = IntegrationPackManager()
    # Desktop automation costs two PowerShell hosts, so it is composed on first
    # use rather than started with the Hub, and there is only ever one.
    app.state.computer = None
    app.state.computer_lock = threading.Lock()

    @app.exception_handler(HubFailure)
    async def handle_hub_error(request: Request, exc: HubFailure):
        return JSONResponse(exc.error.model_dump(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/team/"):
            return JSONResponse({"code": "TEAM_REQUEST_INVALID", "detail": "Check the invitation, name, role and local folder."}, status_code=422)
        if request.url.path.startswith("/api/chat/"):
            return JSONResponse(
                {"code": "CHAT_REQUEST_INVALID", "detail": "Invalid chat request. Check the project, provider and message fields."},
                status_code=422,
            )
        if request.url.path.startswith("/api/fab/"):
            return JSONResponse(
                {"code": "FAB_REQUEST_INVALID", "detail": "Invalid fabrication request. Check the required paths, field types and options."},
                status_code=422,
            )
        if request.url.path.startswith("/api/computer/"):
            return JSONResponse(
                {"code": "COMPUTER_ACTION_INVALID", "detail": "Invalid computer request. Check application/depth, the action object, or command/name."},
                status_code=422,
            )
        if request.url.path.startswith("/api/updates/"):
            return JSONResponse({"code": "UPDATE_REQUEST_INVALID", "detail": "Invalid desktop update request."}, status_code=422)
        if request.url.path.startswith(("/api/credentials", "/api/links/")):
            # Never echo a request here: its body may hold a key.
            return JSONResponse({"code": "CREDENTIAL_REQUEST_INVALID", "detail": "Invalid key request. Send the key as the only field."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    # Recovering a project inspects its retained runs in this process
    # (inspect_runtime), which refuses with the Project Runtime's StudioError.
    @app.exception_handler(StudioError)
    async def handle_runtime_error(request: Request, exc: StudioError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(SettingsError)
    async def handle_settings_error(request: Request, exc: SettingsError):
        return JSONResponse({"code": "SETTINGS_UNAVAILABLE", "detail": "The local settings location is unavailable."}, status_code=503)

    @app.exception_handler(ValidationError)
    async def handle_saved_settings_error(request: Request, exc: ValidationError):
        return JSONResponse({"code": "APP_SETTINGS_INVALID", "detail": "Saved application settings are invalid. Save a valid configuration to replace them."}, status_code=422)

    @app.exception_handler(UnicodeError)
    async def handle_settings_encoding_error(request: Request, exc: UnicodeError):
        return JSONResponse({"code": "APP_SETTINGS_INVALID", "detail": "Saved application settings are not readable UTF-8."}, status_code=422)

    @app.exception_handler(OSError)
    async def handle_local_io_error(request: Request, exc: OSError):
        return JSONResponse({"code": "LOCAL_IO_FAILED", "detail": "The application configuration or log location could not be accessed."}, status_code=503)

    origins = {f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"}
    if settings.web_origin:
        origins.add(settings.web_origin)

    @app.middleware("http")
    async def require_local_origin(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"}:
            return JSONResponse({"code": "LOCAL_HOST_REQUIRED", "detail": "Use the local Hub address."}, status_code=403)
        origin = request.headers.get("origin")
        # Embedded Studio still serves its own assets. Its API attaches to the
        # Hub only while this Hub owns that exact, verified worker origin.
        worker_origins = applications.supervisor.verified_origins("studio")
        allowed_origins = origins | worker_origins
        if origin and origin not in allowed_origins:
            return JSONResponse({"code": "LOCAL_ORIGIN_REQUIRED", "detail": "Use the local Hub page."}, status_code=403)
        if request.method == "OPTIONS" and origin in worker_origins:
            response = Response(status_code=204)
        else:
            try:
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith("/api/updates/"):
                    with updates.mutation():
                        response = await call_next(request)
                else:
                    response = await call_next(request)
            except HubFailure as exc:
                response = JSONResponse(exc.error.model_dump(), status_code=exc.status)
        if origin in worker_origins:
            response.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key, X-Monkey-Operation, X-Monkey-Parent, Last-Event-ID, If-None-Match",
                "Access-Control-Expose-Headers": "ETag, Content-Disposition, X-Monkey-Operation-Id"})
        return response

    if settings.web_origin:
        app.add_middleware(CORSMiddleware, allow_origins=[settings.web_origin], allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])

    # The routes stay in the order the Hub's OpenAPI document and its generated
    # client were written in: each group's routers go in where its routes stood.
    @app.get("/api/health", response_model=HubHealth)
    def health() -> HubHealth:
        return HubHealth(processId=os.getpid(), parentProcessId=os.getppid(), managedInstanceId=settings.managed_instance_id, sourceRevision=applications.source_revision)

    @app.get("/api/integrations", response_model=dict)
    def integration_status(rescan: bool = False) -> dict:
        """Local diagnostic snapshot; rescan is bounded and never qualifies a host."""
        return app.state.integrations.status(rescan=rescan)

    app.include_router(team_router(teams))
    app.include_router(updates_router(updates))
    apps_routes, modeling_routes = apps_and_modeling_routers(settings, applications, chats, runtimes)
    app.include_router(apps_routes)

    fab_errors = {422: {"model": HubError}, 502: {"model": HubError}, 503: {"model": HubError}}

    @app.get("/api/fab/profiles", response_model=dict[str, FabProfile], responses=fab_errors)
    def get_fab_profiles() -> dict[str, FabProfile]:
        return fabrication.profiles()

    @app.post("/api/fab/prepare", response_model=FabPrepareResult, responses=fab_errors)
    def prepare_fab(body: FabPrepareRequest) -> FabPrepareResult:
        return fabrication.prepare(body)

    @app.post("/api/fab/send", response_model=FabSendResult, responses=fab_errors)
    def send_fab(body: FabSendRequest) -> FabSendResult:
        return fabrication.send(body)

    app.include_router(app_settings_router(settings, applications, chats))
    app.include_router(modeling_routes)
    app.include_router(providers_router(chats))
    app.include_router(credentials_router)
    app.include_router(projects_router(chats))

    archive_errors = {404: {"model": HubError}, 409: {"model": HubError}, 422: {"model": HubError}}

    @app.post("/api/project/archive/export", response_model=ProjectArchiveSummary, status_code=201,
              responses=archive_errors)
    def export_project_archive(body: ProjectArchiveExportRequest) -> ProjectArchiveSummary:
        return project_archive.export_archive(body)

    @app.post("/api/project/archive/restore", response_model=ProjectArchiveRestoreResult, status_code=201,
              responses=archive_errors)
    def restore_project_archive(body: ProjectArchiveRestoreRequest) -> ProjectArchiveRestoreResult:
        return project_archive.restore_archive(body, runtime_root=settings.runtime_root)

    app.include_router(sessions_router(chats))

    computer_errors = {403: {"model": HubError}, 409: {"model": HubError}, 422: {"model": HubError}}

    def computer() -> ComputerService:
        """The one service this Hub owns, built the first time it is asked for."""
        with app.state.computer_lock:
            if app.state.computer is None:
                app.state.computer = ComputerService(settings.runtime_root)
            return app.state.computer

    @app.post("/api/computer/inspect", responses=computer_errors)
    def inspect_computer(body: ComputerInspectRequest) -> dict:
        return computer().inspect(body)

    @app.post("/api/computer/actions", responses=computer_errors)
    def act_on_computer(body: ComputerActionRequest) -> dict:
        # A refused or failed receipt is this body, not a status: the caller
        # reads the refusal it earned. Only an invalid action and a runtime
        # level refusal have no receipt to carry them.
        return computer().act(body)

    @app.post("/api/computer/recordings", responses=computer_errors)
    def record_computer(body: ComputerRecordingRequest) -> dict:
        return computer().record(body)

    app.include_router(runtime_router(runtimes))
    app.include_router(preferences_router, prefix="/api")
    if settings.hub_web_dir is not None:
        if not (settings.hub_web_dir / "index.html").is_file():
            raise ValueError("hub_web_dir must contain the built Hub index.html")
        app.mount("/", StaticFiles(directory=settings.hub_web_dir, html=True), name="hub-web")
    return app
