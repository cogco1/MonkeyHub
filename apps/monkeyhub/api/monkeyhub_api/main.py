"""A project-free local host for the existing application services."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
from uuid import UUID
import webbrowser

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

from .applications import Applications
from .fabrication import Fabrication
from .models import (
    AppId, AppStatus, FabPrepareRequest, FabPrepareResult, FabProfile,
    FabSendRequest, FabSendResult, HubError, HubFailure, HubHealth,
)

SOURCE_ROOT = Path(__file__).resolve().parents[4]


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

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(applications.shutdown)

    app = FastAPI(title="MonkeyHub API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.applications = applications

    @app.exception_handler(HubFailure)
    async def handle_hub_error(request: Request, exc: HubFailure):
        return JSONResponse(exc.error.model_dump(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: RequestValidationError):
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
        if origin and origin not in origins:
            return JSONResponse({"code": "LOCAL_ORIGIN_REQUIRED", "detail": "Use the local Hub page."}, status_code=403)
        return await call_next(request)

    if settings.web_origin:
        app.add_middleware(CORSMiddleware, allow_origins=[settings.web_origin], allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])

    @app.get("/api/health", response_model=HubHealth)
    def health() -> HubHealth:
        return HubHealth(processId=os.getpid(), parentProcessId=os.getppid(), managedInstanceId=settings.managed_instance_id, sourceRevision=applications.source_revision)

    @app.get("/api/apps", response_model=list[AppStatus])
    def list_apps() -> list[AppStatus]:
        return applications.statuses()

    error_responses = {409: {"model": HubError}, 503: {"model": HubError}}

    @app.post("/api/apps/{app_id}/start", response_model=AppStatus, status_code=202, responses=error_responses)
    def start_app(app_id: AppId) -> AppStatus:
        return applications.start(app_id)

    @app.post("/api/apps/{app_id}/stop", response_model=AppStatus, status_code=202, responses=error_responses)
    def stop_app(app_id: AppId) -> AppStatus:
        return applications.stop(app_id)

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
        return applications.configure(body)

    app.include_router(preferences_router, prefix="/api")
    if settings.hub_web_dir is not None:
        if not (settings.hub_web_dir / "index.html").is_file():
            raise ValueError("hub_web_dir must contain the built Hub index.html")
        app.mount("/", StaticFiles(directory=settings.hub_web_dir, html=True), name="hub-web")
    return app


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
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port))
    if args.managed_stdin:
        def watch_stdin():
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
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
