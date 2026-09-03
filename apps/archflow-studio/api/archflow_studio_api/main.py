"""The Studio API application: settings in, FastAPI app out.

``create_app`` touches no filesystem and binds no project. Constructing the app
is not the moment to discover that a project root is wrong; the request that
needs the project is.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
import uvicorn

from . import routes
from .settings import PROJECT_DIR_ENV, StudioSettings
from .transport.errors import BlockedNeedsHuman, StudioError, StudioErrorDto

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

_HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}

UNEXPECTED_ERROR_DETAIL = (
    "The API failed while handling this request. The traceback is in the "
    "service log, not on the wire."
)


def _error_payload(
    code: str,
    detail: str,
    *,
    question: str | None = None,
    accepted_forms: list[str] | None = None,
) -> dict[str, object]:
    """Build the wire body through the declared shape, so the two cannot drift."""

    return StudioErrorDto(
        code=code,
        detail=detail,
        question=question,
        accepted_forms=accepted_forms,
    ).model_dump(by_alias=True, exclude_none=True)


async def _handle_studio_error(
    request: Request,
    exc: StudioError,
) -> JSONResponse:
    if isinstance(exc, BlockedNeedsHuman):
        content = _error_payload(
            exc.code,
            exc.detail,
            question=exc.question,
            accepted_forms=list(exc.accepted_forms),
        )
    else:
        content = _error_payload(exc.code, exc.detail)
    return JSONResponse(status_code=exc.status, content=content)


async def _handle_http_exception(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Answer FastAPI's own 404s and 405s in the one error shape too."""

    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(code, str(exc.detail)),
        # A 405 carries Allow, a 401 carries WWW-Authenticate: the one error
        # shape must not cost the client the header that says what to do next.
        headers=getattr(exc, "headers", None),
    )


async def _handle_unexpected_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Even a bug answers in the one shape, and says nothing about itself."""

    return JSONResponse(
        status_code=500,
        content=_error_payload("INTERNAL_ERROR", UNEXPECTED_ERROR_DETAIL),
    )


def _summarize_validation(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ()))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "the request could not be read"


async def _handle_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_payload("REQUEST_INVALID", _summarize_validation(exc)),
    )


def create_app(settings: StudioSettings) -> FastAPI:
    """Build the application. No filesystem, no project, no I/O."""

    app = FastAPI(title="ArchFlow Studio API", version="0.1.0")
    app.state.settings = settings
    app.add_exception_handler(StudioError, _handle_studio_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(routes.router)
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archflow-studio-api",
        description="Serve the ArchFlow Studio API.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help=f"Project root to bind; overrides {PROJECT_DIR_ENV}.",
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> StudioSettings:
    """Resolve settings for one invocation: the flag beats the variable."""

    if args.project_dir is not None:
        # The remaining settings keep coming from the environment through the
        # single reader in settings.py.
        os.environ[PROJECT_DIR_ENV] = str(args.project_dir)
    return StudioSettings.from_env()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
