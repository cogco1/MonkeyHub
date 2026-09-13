"""A project-free local host for the existing application services."""

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import sys
import threading
import time
import queue
import socket
from urllib.parse import urlsplit
from uuid import UUID
import webbrowser

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
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
from .applications import Applications
from .chat import ChatStore
from .runtime import ProjectRuntimeManager
from .runtime_models import HubRuntimeDto, ProjectRuntimeDto, OpenRuntimeRequest, RuntimeProjectRequest, RuntimeEvent
from .fabrication import Fabrication
from .models import (
    AppId, AppStatus, FabPrepareRequest, FabPrepareResult, FabProfile,
    FabSendRequest, FabSendResult, HubError, HubFailure, HubHealth,
    ChatProvider, ChatProject, ChatProjectRequest, ChatSummary, ChatDetail, ChatCreateRequest,
    ChatModelRequest, ChatPostRequest, ChatUsageSource, ChatWorkspace, ChatPermissionRequest, ChatArchiveRequest,
)

SOURCE_ROOT = Path(__file__).resolve().parents[4]


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
    studio_web_dir: Path | None = None
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
    applications = Applications(source_root, settings.runtime_root, settings.studio_web_dir, settings.port)
    fabrication = Fabrication(source_root)
    chats = ChatStore(settings.runtime_root, f"http://127.0.0.1:{settings.port}", applications=applications)
    runtimes = ProjectRuntimeManager(applications, chats)
    chats.on_change = runtimes.chat_changed

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.to_thread(applications.start, "monkeymonitor")
        except (HubFailure, OSError, SettingsError, ValidationError, UnicodeError) as exc:
            # A launch failure must leave the Hub available for configuration
            # and an explicit retry through the existing application route.
            logging.getLogger(__name__).warning("MonkeyMonitor could not be prepared: %s", exc)
        yield
        await asyncio.to_thread(chats.shutdown)
        await asyncio.to_thread(applications.shutdown)
        await asyncio.to_thread(runtimes.shutdown)

    app = FastAPI(title="MonkeyHub API", version="0.1.0", lifespan=lifespan, servers=[{"url": "/"}])
    app.state.settings = settings
    app.state.applications = applications
    app.state.chats = chats
    app.state.runtimes = runtimes
    app.state.studio_event_sockets = set()

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
        worker_origins = {row.url.rstrip("/") for row in applications.worker_snapshots()
                          if row.url and row.healthy and row.service_id == "studio"}
        allowed_origins = origins | worker_origins
        if origin and origin not in allowed_origins:
            return JSONResponse({"code": "LOCAL_ORIGIN_REQUIRED", "detail": "Use the local Hub page."}, status_code=403)
        if request.method == "OPTIONS" and origin in worker_origins:
            response = Response(status_code=204)
        else:
            response = await call_next(request)
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

    @app.get("/api/apps", response_model=list[AppStatus])
    def list_apps(projectDir: str | None = None) -> list[AppStatus]:
        return applications.statuses(project_dir=projectDir)

    error_responses = {409: {"model": HubError}, 503: {"model": HubError}}

    @app.post("/api/apps/{app_id}/start", response_model=AppStatus, status_code=202, responses=error_responses)
    def start_app(app_id: AppId, projectDir: str | None = None) -> AppStatus:
        runtime = None
        if app_id in {"monkeyarch", "monkeydiagram", "monkeyboard"}:
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

    @app.get("/api/chat/sessions", response_model=list[ChatSummary])
    def chat_sessions(projectId: str | None = None, archived: bool = False):
        return chats.list(projectId, archived=archived)

    @app.get("/api/chat/usage-sources", response_model=list[ChatUsageSource])
    def chat_usage_sources():
        return chats.usage_sources()

    @app.post("/api/chat/sessions", response_model=ChatDetail, status_code=201)
    def create_chat(body: ChatCreateRequest):
        return chats.create(body)

    @app.get("/api/chat/sessions/{session_id}", response_model=ChatDetail)
    def read_chat(session_id: str):
        return chats.get(session_id)

    @app.post("/api/chat/sessions/{session_id}/messages", response_model=ChatDetail, status_code=202)
    def post_chat(session_id: str, body: ChatPostRequest):
        return chats.post(session_id, body)

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
    parser.add_argument("--studio-web-dir", type=Path)
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
    studio_web = args.studio_web_dir or SOURCE_ROOT / "apps/archflow-studio/web/dist"
    settings = HubSettings(
        runtime_root=runtime_root, port=args.port, hub_web_dir=hub_web,
        studio_web_dir=studio_web, web_origin=args.web_origin,
        managed_instance_id=str(args.managed_instance_id) if args.managed_instance_id else None,
    )
    with _runtime_lease(settings.runtime_root):
        app = create_app(settings)
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
        server.run()


if __name__ == "__main__":
    main()
