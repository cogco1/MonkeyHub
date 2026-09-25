"""A project-free local host for the existing application services."""

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import inspect
import logging
import os
from pathlib import Path
import sys
import threading
import time
import queue
import socket
from urllib.parse import urlsplit
from urllib.parse import quote
from uuid import UUID
import webbrowser

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.requests import Request
import uvicorn

from archflow_studio_api.routes.settings import router as preferences_router
from archflow_studio_api.settings import (
    SettingsError, read_application_settings,
)
from archflow_studio_api.transport.errors import StudioError
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from archflow_studio_api.transport.project import ModelingInitializeDto, ModelingInitializeRequestDto

from . import chat as chat_tools
from . import project_archive
from .applications import Applications
from .chat import ChatStore
from .computer_tools import ComputerService
from .runtime import ProjectRuntimeManager
from .runtime_models import HubRuntimeDto, ProjectRuntimeDto, OpenRuntimeRequest, RuntimeProjectRequest, RuntimeEvent
from .fabrication import Fabrication
from .updates import (
    DesktopUpdates, UpdateStatus, CompleteUpdate, RollbackUpdate, UpdateSettings, MAX_PATCH_BYTES,
    recover_failed_start,
)
from .models import (
    AppId, AppStatus, FabPrepareRequest, FabPrepareResult, FabProfile,
    FabSendRequest, FabSendResult, HubError, HubFailure, HubHealth,
    ChatProvider, ChatProject, ChatProjectRequest, ChatSummary, ChatDetail, ChatCreateRequest,
    ChatModelRequest, ChatPostRequest, ChatUsageSource, ChatWorkspace, ChatPermissionRequest, ChatArchiveRequest,
    ChatAttachmentContent,
    ChatPresentationBindRequest, ChatPresentationBinding, ChatPresentationRequest,
    ProjectArchiveExportRequest, ProjectArchiveRestoreRequest, ProjectArchiveRestoreResult, ProjectArchiveSummary,
    ComputerActionRequest, ComputerInspectRequest, ComputerRecordingRequest,
)

SOURCE_ROOT = Path(__file__).resolve().parents[4]


def complete_interrupted_connection_teardown() -> None:
    """A connection the proactor transport cannot close must not stay attached.

    CPython shuts the socket down inside the `finally` of
    `_call_connection_lost`. Windows answers WinError 10054 when the peer is
    already gone, and that error leaves the rest of that block unrun: the
    socket stays open and the connection stays attached to `asyncio.Server`.
    `Server.wait_closed()` then waits for it forever, so uvicorn logs
    `Shutting down`, never reaches the ASGI lifespan shutdown and never exits,
    leaving the desktop host waiting on a stop it already requested. Finish
    the skipped teardown exactly once and re-raise the operating system's
    error, so nothing is detached twice and nothing is hidden.
    """
    from asyncio.proactor_events import _ProactorBasePipeTransport as Transport

    interrupted = Transport._call_connection_lost
    if getattr(interrupted, "_completes_teardown", False):
        return

    def _call_connection_lost(self, exc):
        try:
            interrupted(self, exc)
        except OSError:
            # Take each field before using it, so this teardown stays single
            # use however far CPython's own block reached before it raised.
            closing, self._sock = self._sock, None
            server, self._server = self._server, None
            self._called_connection_lost = True
            if closing is not None:
                try:
                    closing.close()
                except OSError:
                    pass
            if server is not None:
                # 3.12 detaches a counted connection; 3.13 discards the transport.
                if inspect.signature(server._detach).parameters:
                    server._detach(self)
                else:
                    server._detach()
            raise

    _call_connection_lost._completes_teardown = True
    Transport._call_connection_lost = _call_connection_lost


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


class HubServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # Uvicorn drains HTTP tasks before entering ASGI lifespan shutdown.
        # End subscriptions first; admitted mutations still drain normally.
        app = self.config.app
        app.state.runtimes.begin_shutdown()
        for stream_socket in tuple(app.state.studio_event_sockets):
            try:
                stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        await super().shutdown(sockets=sockets)


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
        yield
        await asyncio.to_thread(updates.shutdown)
        await asyncio.to_thread(chats.shutdown)
        await asyncio.to_thread(applications.shutdown)
        await asyncio.to_thread(runtimes.shutdown)
        await asyncio.to_thread(_close_computer, app)

    app = FastAPI(title="MonkeyHub API", version="0.1.0", lifespan=lifespan, servers=[{"url": "/"}])
    app.state.settings = settings
    app.state.applications = applications
    app.state.chats = chats
    app.state.runtimes = runtimes
    app.state.updates = updates
    app.state.studio_event_sockets = set()
    # Desktop automation costs two PowerShell hosts, so it is composed on first
    # use rather than started with the Hub, and there is only ever one.
    app.state.computer = None
    app.state.computer_lock = threading.Lock()

    @app.exception_handler(HubFailure)
    async def handle_hub_error(request: Request, exc: HubFailure):
        return JSONResponse(exc.error.model_dump(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: RequestValidationError):
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
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StudioError)
    async def handle_preferences_error(request: Request, exc: StudioError):
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

    @app.get("/api/health", response_model=HubHealth)
    def health() -> HubHealth:
        return HubHealth(processId=os.getpid(), parentProcessId=os.getppid(), managedInstanceId=settings.managed_instance_id, sourceRevision=applications.source_revision)

    @app.get("/api/updates/status", response_model=UpdateStatus)
    def update_status() -> UpdateStatus:
        return updates.status()

    @app.get("/api/updates/restart")
    def update_restart() -> dict:
        return updates.restart()

    @app.post("/api/updates/prepare", response_model=UpdateStatus, status_code=202)
    async def prepare_update(request: Request) -> UpdateStatus:
        if request.headers.get("x-monkeyhub-local-patch") != "1":
            raise HubFailure(422, "LOCAL_PATCH_REQUIRED", "Choose a local developer patch explicitly.")
        path = updates.begin_upload()
        try:
            size = 0
            with path.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PATCH_BYTES:
                        raise HubFailure(413, "UPDATE_PATCH_TOO_LARGE", "A patch must be no larger than 256 MiB.")
                    output.write(chunk)
            if size == 0:
                raise HubFailure(422, "UPDATE_PATCH_EMPTY", "Choose a nonempty patch ZIP.")
        except (Exception, asyncio.CancelledError) as error:
            updates.fail_upload(path, str(error))
            raise
        updates.prepare(path)
        return updates.status()

    @app.post("/api/updates/apply", response_model=UpdateStatus, status_code=202)
    def apply_update() -> UpdateStatus:
        return updates.apply()

    @app.post("/api/updates/complete", response_model=UpdateStatus)
    def complete_update(body: CompleteUpdate) -> UpdateStatus:
        return updates.complete(body.fromCommit)

    @app.post("/api/updates/rollback", response_model=UpdateStatus)
    def rollback_update(body: RollbackUpdate) -> UpdateStatus:
        return updates.rollback(body.fromCommit, body.targetCommit)

    @app.post("/api/updates/check", response_model=UpdateStatus, status_code=202)
    def check_updates() -> UpdateStatus:
        return updates.check_now()

    @app.put("/api/updates/settings", response_model=UpdateStatus)
    def update_settings(body: UpdateSettings) -> UpdateStatus:
        return updates.set_auto_update(body.autoUpdate)

    @app.get("/api/apps", response_model=list[AppStatus])
    def list_apps(projectDir: str | None = None) -> list[AppStatus]:
        return applications.statuses(project_dir=projectDir)

    error_responses = {409: {"model": HubError}, 503: {"model": HubError}}

    @app.post("/api/apps/{app_id}/start", response_model=AppStatus, status_code=202, responses=error_responses)
    def start_app(app_id: AppId, projectDir: str | None = None) -> AppStatus:
        runtime = None
        if app_id in {"monkeyarch", "monkeyboard"}:
            target = projectDir or read_application_settings(settings.runtime_root).project_dir
            if target:
                project_id, target = chat_tools._project(target)
                runtime = runtimes.open(project_id, target)
        with chats.application_lifecycle(app_id, project_dir=projectDir):
            if runtime is not None and any(row.state == "crashed" for row in applications.worker_snapshots(project_dir=runtime.project_dir)):
                runtimes.recover(runtime)
                return applications.status(app_id, project_dir=runtime.project_dir)
            return applications.start(app_id, project_dir=projectDir)

    @app.post("/api/apps/{app_id}/stop", response_model=AppStatus, status_code=202, responses=error_responses)
    def stop_app(app_id: AppId, projectDir: str | None = None) -> AppStatus:
        with chats.application_lifecycle(app_id, stopping=True, project_dir=projectDir):
            return applications.stop(app_id, project_dir=projectDir)

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

    @app.get("/api/settings/apps", response_model=ApplicationSettingsDto, response_model_by_alias=True)
    def application_settings() -> ApplicationSettingsDto:
        return read_application_settings(settings.runtime_root)

    @app.put("/api/settings/apps", response_model=ApplicationSettingsDto, response_model_by_alias=True)
    def update_application_settings(body: ApplicationSettingsDto) -> ApplicationSettingsDto:
        with chats.project_configuration(body.project_dir):
            return applications.configure(body)

    @app.post("/api/project/modeling", response_model=ModelingInitializeDto, response_model_by_alias=True)
    def prepare_project_modeling(body: ModelingInitializeRequestDto, projectDir: str | None = None) -> dict:
        target = projectDir if projectDir is not None else read_application_settings(settings.runtime_root).project_dir
        if not target:
            raise HubFailure(409, "PROJECT_REQUIRED", "Choose a project before preparing its modeling workspace.")
        project_id, project_dir = chat_tools._project(target)
        if project_id != body.project_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The selected project changed before its workspace was prepared.")
        status = start_app("monkeyarch", projectDir=project_dir)
        deadline = time.monotonic() + 35
        while status.state == "starting" and time.monotonic() < deadline:
            time.sleep(0.1)
            status = applications.status("monkeyarch", project_dir=project_dir)
        if status.state != "running":
            if status.error is not None:
                raise HubFailure(503, status.error.code, status.error.detail)
            raise HubFailure(503, "CHAT_STUDIO_UNAVAILABLE", "The project workspace is not ready. Retry preparing this project.")
        base, binding = chat_tools._bound_studio(chats.hub_url, None, project_id=project_id, project_dir=project_dir)
        prepared = chat_tools._request_json(base, "/api/project/modeling", "POST", {"projectId": binding["projectId"]})
        runtimes.open(project_id, project_dir)
        return prepared

    @app.get("/api/chat/providers", response_model=list[ChatProvider])
    def chat_providers(refresh: bool = False):
        return chats.providers(refresh)

    @app.get("/api/chat/projects", response_model=list[ChatProject])
    def chat_projects():
        return chats.projects()

    @app.get("/api/chat/workspace", response_model=ChatWorkspace)
    def chat_workspace():
        return chats.workspace()

    @app.post("/api/chat/projects", response_model=ChatProject, status_code=201)
    def create_chat_project(body: ChatProjectRequest):
        return chats.create_project(body)

    archive_errors = {404: {"model": HubError}, 409: {"model": HubError}, 422: {"model": HubError}}

    @app.post("/api/project/archive/export", response_model=ProjectArchiveSummary, status_code=201,
              responses=archive_errors)
    def export_project_archive(body: ProjectArchiveExportRequest) -> ProjectArchiveSummary:
        return project_archive.export_archive(body)

    @app.post("/api/project/archive/restore", response_model=ProjectArchiveRestoreResult, status_code=201,
              responses=archive_errors)
    def restore_project_archive(body: ProjectArchiveRestoreRequest) -> ProjectArchiveRestoreResult:
        return project_archive.restore_archive(body, runtime_root=settings.runtime_root)

    @app.get("/api/chat/sessions", response_model=list[ChatSummary])
    def chat_sessions(projectId: str | None = None, archived: bool = False):
        return chats.list(projectId, archived=archived)

    @app.get("/api/chat/usage-sources", response_model=list[ChatUsageSource])
    def chat_usage_sources():
        return chats.usage_sources()

    @app.post("/api/chat/sessions", response_model=ChatDetail, status_code=201)
    def create_chat(body: ChatCreateRequest):
        return chats.create(body)

    def local_presenter(request: Request) -> None:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HubFailure(403, "LOCAL_PRESENTER_REQUIRED", "Presentation tools must connect from this machine.")

    @app.post("/api/chat/presentation/bind", response_model=ChatPresentationBinding)
    def bind_chat_presentation(request: Request, body: ChatPresentationBindRequest):
        local_presenter(request)
        return chats.bind_presentation(body)

    @app.post("/api/chat/sessions/{session_id}/presentation", response_model=ChatDetail)
    def present_chat(request: Request, session_id: str, body: ChatPresentationRequest):
        local_presenter(request)
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return chats.present(session_id, body, token)

    @app.get("/api/chat/sessions/{session_id}/documents/{message_id}/{index}", response_class=Response)
    def read_chat_document(session_id: str, message_id: str, index: int, download: bool = False):
        document, data = chats.presentation_document(session_id, message_id, index)
        safe_inline = document.mime_type in chat_tools._IMAGE_MIMES | {"application/pdf"}
        disposition = "attachment" if download or not safe_inline else "inline"
        return Response(data, media_type=document.mime_type if safe_inline else "application/octet-stream",
                        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(document.file_name, safe='')}",
                                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                 "ETag": f'"{document.asset_sha256}"'})

    @app.get("/api/chat/sessions/{session_id}", response_model=ChatDetail)
    def read_chat(session_id: str):
        return chats.get(session_id)

    @app.post("/api/chat/sessions/{session_id}/messages", response_model=ChatDetail, status_code=202)
    def post_chat(session_id: str, body: ChatPostRequest):
        return chats.post(session_id, body)

    @app.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}", response_class=FileResponse)
    def read_chat_attachment(session_id: str, attachment_id: str, inline: bool = False):
        attachment, path = chats.attachment(session_id, attachment_id)
        if inline:
            if attachment.mimeType not in chat_tools._IMAGE_MIMES:
                raise HubFailure(422, "CHAT_IMAGE_INVALID", "Only supported raster images can be previewed inline.")
            chat_tools._verify_image(path.read_bytes(), attachment.mimeType)
        return FileResponse(path, media_type=attachment.mimeType if inline else "application/octet-stream", filename=attachment.name,
                            content_disposition_type="inline" if inline else "attachment",
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}/model-source")
    def read_chat_model_source(session_id: str, attachment_id: str):
        import base64
        attachment, path = chats.attachment(session_id, attachment_id)
        if path.suffix.lower() not in {".3dm", ".glb", ".skp", ".dwg"}:
            raise HubFailure(422, "CHAT_MODEL_SOURCE_INVALID", "Choose a 3DM, GLB, SKP or DWG attachment.")
        return {"fileName": attachment.name, "attachmentId": attachment.id,
                "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii")}

    @app.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}/read", response_model=ChatAttachmentContent)
    def read_chat_attachment_content(session_id: str, attachment_id: str, offset: int = Query(0, ge=0),
                                     limit: int = Query(32768, ge=1, le=65536), page: int = Query(1, ge=1)):
        return chats.read_attachment(session_id, attachment_id, offset=offset, limit=limit, page=page)

    @app.put("/api/chat/sessions/{session_id}/model", response_model=ChatDetail)
    def set_chat_model(session_id: str, body: ChatModelRequest):
        return chats.set_model(session_id, body.model)

    @app.put("/api/chat/sessions/{session_id}/archive", response_model=ChatDetail)
    def set_chat_archived(session_id: str, body: ChatArchiveRequest):
        return chats.set_archived(session_id, body.archived)

    @app.post("/api/chat/sessions/{session_id}/stop", response_model=ChatDetail)
    def stop_chat(session_id: str):
        return chats.stop(session_id)

    @app.post("/api/chat/sessions/{session_id}/permissions/{permission_id}", response_model=ChatDetail)
    def resolve_chat_permission(session_id: str, permission_id: str, body: ChatPermissionRequest):
        return chats.resolve_permission(session_id, permission_id, body)

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

    @app.get("/api/runtime", response_model=HubRuntimeDto)
    def read_runtime():
        runtimes.discover()
        return runtimes.snapshot()

    @app.post("/api/runtime/projects/open", response_model=ProjectRuntimeDto)
    def open_runtime(body: OpenRuntimeRequest):
        runtime = runtimes.open(body.projectId, body.projectDir)
        return runtimes.project_snapshot(runtime)

    @app.get("/api/runtime/projects/{runtime_id}", response_model=ProjectRuntimeDto)
    def read_project_runtime(runtime_id: str):
        return runtimes.project_snapshot(runtimes.get(runtime_id))

    @app.post("/api/runtime/projects/{runtime_id}/recover", response_model=ProjectRuntimeDto, status_code=202)
    def recover_runtime(runtime_id: str, body: RuntimeProjectRequest):
        return runtimes.recover(runtimes.get(runtime_id, body.projectId))

    @app.post("/api/runtime/projects/{runtime_id}/close", response_model=ProjectRuntimeDto, status_code=202)
    def close_runtime(runtime_id: str, body: RuntimeProjectRequest):
        return runtimes.close(runtimes.get(runtime_id, body.projectId))

    @app.get("/api/runtime/events", response_class=StreamingResponse, response_model=RuntimeEvent,
             responses={200: {"description": "Runtime SSE; every attachment begins with a coherent snapshot.",
                              "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def runtime_events(request: Request):
        await asyncio.to_thread(runtimes.discover)

        async def stream():
            # Subscribe before reading the snapshot. Drop queued events the
            # snapshot already includes, so no change can fall through a gap.
            with runtimes.events.subscribe() as (_, inbox):
                runtimes._clients += 1
                try:
                    snapshot = await asyncio.to_thread(runtimes.snapshot)
                    sequence = snapshot.sequence
                    event = RuntimeEvent(serverId=runtimes.server_id, sequence=sequence, kind="runtime/snapshot", snapshot=snapshot)
                    yield f"id: {runtimes.server_id}:{sequence}\nevent: runtime\ndata: {event.model_dump_json()}\n\n"
                    while not await request.is_disconnected() and not runtimes._closing.is_set():
                        try:
                            value = await asyncio.to_thread(inbox.get, True, 1)
                        except queue.Empty:
                            yield ": keepalive\n\n"
                            continue
                        if value["seq"] <= sequence:
                            continue
                        sequence = value["seq"]
                        event = RuntimeEvent(serverId=runtimes.server_id, sequence=sequence, kind=value["kind"], runtimeId=value.get("runtimeId"))
                        yield f"id: {runtimes.server_id}:{sequence}\nevent: runtime\ndata: {event.model_dump_json()}\n\n"
                finally:
                    runtimes._clients -= 1

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/api/runtime/projects/{runtime_id}/studio/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE"], include_in_schema=False)
    async def studio_request(request: Request, runtime_id: str, path: str):
        runtime = await asyncio.to_thread(runtimes.get, runtime_id)
        target = "/" + path + (f"?{request.url.query}" if request.url.query else "")
        if path == "api/events" and request.method == "GET":
            # Preserve the existing Studio job stream for embedded clients.
            # The Hub's application stream above remains their runtime source.
            from http.client import HTTPConnection, HTTPException
            worker = runtimes.service(runtime)
            address = urlsplit(worker.url)
            connection = HTTPConnection(address.hostname, address.port, timeout=20)

            def attach():
                connection.connect()
                stream_socket = connection.sock
                try:
                    connection.request("GET", target, headers={"Last-Event-ID": request.headers.get("last-event-id", "")})
                    return connection.getresponse(), stream_socket
                except Exception:
                    connection.close()
                    raise

            upstream, stream_socket = await asyncio.to_thread(attach)
            app.state.studio_event_sockets.add(stream_socket)

            async def chunks():
                try:
                    while not runtimes._closing.is_set() and not await request.is_disconnected():
                        line = await asyncio.to_thread(upstream.readline)
                        if not line:
                            break
                        yield line
                except (OSError, HTTPException):
                    # A dead/replaced worker ends this attachment. The browser
                    # reconnects; no operation is admitted by this read stream.
                    return
                finally:
                    try:
                        stream_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    upstream.close()
                    connection.close()
                    app.state.studio_event_sockets.discard(stream_socket)
            return StreamingResponse(chunks(), status_code=upstream.status, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        result = await asyncio.to_thread(runtimes.forward, runtime, target, request.method,
                                         await request.body(), dict(request.headers))
        return Response(result.body, status_code=result.status, headers=result.headers)

    app.include_router(preferences_router, prefix="/api")
    if settings.hub_web_dir is not None:
        if not (settings.hub_web_dir / "index.html").is_file():
            raise ValueError("hub_web_dir must contain the built Hub index.html")
        app.mount("/", StaticFiles(directory=settings.hub_web_dir, html=True), name="hub-web")
    return app


@contextmanager
def _runtime_lease(root: Path):
    """Keep both CLI entrypoints exclusive until the actual Hub finishes draining."""
    root.mkdir(parents=True, exist_ok=True)
    # Keep the same inode after release; deleting a lock file would let another
    # process lock a replacement while a previous opener still owns the old one.
    with (root / "hub.lock").open("a+b") as lease:
        try:
            if os.name == "nt":
                import msvcrt
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(f"Another Hub is using runtime directory {root}. Close it and wait for shutdown, or choose another --runtime-root.") from error
        try:
            yield
        finally:
            if os.name == "nt":
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lease.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Open the local MonkeyHub without a building project.")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--hub-web-dir", type=Path)
    parser.add_argument("--web-origin", help="One explicit loopback web development origin")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--managed-stdin", action="store_true")
    parser.add_argument("--managed-instance-id", type=UUID)
    args = parser.parse_args(argv)
    if args.managed_stdin != (args.managed_instance_id is not None):
        parser.error("--managed-stdin and --managed-instance-id must be provided together")
    runtime_root = args.runtime_root
    if runtime_root is None:
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_appdata or not Path(local_appdata).is_absolute():
            parser.error("set --runtime-root to an absolute nonproject directory")
        runtime_root = Path(local_appdata) / "MonkeyHub"
    hub_web = args.hub_web_dir
    if hub_web is None and (SOURCE_ROOT / "apps/monkeyhub/web/dist/index.html").is_file():
        hub_web = SOURCE_ROOT / "apps/monkeyhub/web/dist"
    settings = HubSettings(
        runtime_root=runtime_root, port=args.port, hub_web_dir=hub_web,
        web_origin=args.web_origin,
        managed_instance_id=str(args.managed_instance_id) if args.managed_instance_id else None,
    )
    if sys.platform == "win32":
        complete_interrupted_connection_teardown()
    with _runtime_lease(settings.runtime_root):
        managed = settings.managed_instance_id is not None
        try:
            app = create_app(settings)
        except BaseException as error:
            recover_failed_start(SOURCE_ROOT, settings.runtime_root, managed=managed,
                                 reason=f"{type(error).__name__}: {error}"[:300])
            raise
        server = HubServer(uvicorn.Config(app, host="127.0.0.1", port=settings.port))
        if args.managed_stdin:
            def watch_stdin():
                for line in sys.stdin:
                    if line.strip() == "stop":
                        break
                app.state.chats.shutdown()
                app.state.applications.begin_shutdown()
                server.should_exit = True
            threading.Thread(target=watch_stdin, daemon=True).start()
        if not args.no_browser:
            def open_when_ready():
                while not server.started and not server.should_exit:
                    threading.Event().wait(0.1)
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{settings.port}/")
            threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            server.run()
        finally:
            if not server.started:
                app.state.updates.startup_failed("the Hub server did not start")
        if server.started:
            # A normal quit, with every owned worker drained and the runtime
            # lease still held: switch the desktop entry to a ready update.
            app.state.updates.activate_on_quit()


if __name__ == "__main__":
    main()
