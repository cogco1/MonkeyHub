"""The Studio API application: settings in, FastAPI app out.

``create_app`` touches no filesystem and binds no project; the request that
needs the project is where a wrong project root is discovered.
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
import uvicorn

from . import routes
from .application.events import StudioEvents
from .application.intent_agent import compiler_from_settings
from .application.jobs import JobRegistry
from .application.proposals import ProposalStore
from .application.validation import ValidationStore
from .settings import PROJECT_DIR_ENV, StudioSettings
from .transport.errors import StudioError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
_HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Let a candidate that is already running finish before the process ends.

    A candidate run writes P036 records; killing its thread mid-run would
    leave a run directory nobody can account for. Shutting the worker down and
    waiting is the difference between a service that stops and one that stops
    cleanly.
    """

    yield
    app.state.jobs.shutdown()


def create_app(settings: StudioSettings) -> FastAPI:
    app = FastAPI(
        title="ArchFlow Studio API", version="0.1.0", lifespan=_lifespan
    )
    app.state.settings = settings
    # Proposals live in this process and nowhere else. The store is created
    # here so that fact is visible at the top of the application rather than
    # accumulating quietly at the bottom of a route.
    app.state.proposals = ProposalStore()
    # The event sink is the reserved ``StudioEventSink`` port, and the job
    # registry owns the one worker thread candidates run on. Both are created
    # here for the same reason as the store: what this process holds in memory,
    # and therefore loses on restart, is stated at the top of the application.
    app.state.events = StudioEvents()
    app.state.jobs = JobRegistry(app.state.events, max_workers=settings.workers)
    # One validation per candidate, remembered so that reading a verdict twice
    # is one verdict and one event rather than two of each. In memory, like
    # everything above it, and lost on restart for the same reason.
    app.state.validations = ValidationStore()
    # Who compiles an architect's sentence into the grammar: nobody (the
    # deterministic pass-through), a local codex process, or the Anthropic
    # API — chosen by the settings this app was built with, held here so a
    # test can put a scripted compiler in its place and the route stays one
    # code path. A provider that cannot name its own version refuses here,
    # before a request arrives, rather than at the first sentence.
    app.state.intent_compiler = compiler_from_settings(settings)
    app.add_exception_handler(StudioError, _handle_studio_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(routes.router)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="archflow-studio-api", description="Serve the ArchFlow Studio API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help=f"Project root to bind; overrides {PROJECT_DIR_ENV}.",
    )
    args = parser.parse_args(argv)
    if args.project_dir is not None:
        os.environ[PROJECT_DIR_ENV] = str(args.project_dir)
    uvicorn.run(create_app(StudioSettings.from_env()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
