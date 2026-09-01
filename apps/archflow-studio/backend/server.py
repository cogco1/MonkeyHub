"""Dependency-free local HTTP adapter for the Studio read model."""

from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeAlias
from urllib.parse import urlsplit

from backend.contracts import capabilities_payload
from backend.kernel import ArchFlowKernelFacade


JsonObject: TypeAlias = dict[str, Any]


class StudioRequestHandler(SimpleHTTPRequestHandler):
    server_version = "ArchFlowStudio/0.1"

    def __init__(
        self,
        *args: Any,
        kernel: ArchFlowKernelFacade,
        static_dir: Path,
        **kwargs: Any,
    ) -> None:
        self._kernel = kernel
        self._static_dir = static_dir
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, self._kernel.health().to_dict())
            return
        if path == "/api/capabilities":
            self._send_json(
                200,
                capabilities_payload(self._kernel.capabilities()),
            )
            return
        if path == "/api/session":
            self._send_json(200, self._kernel.session().to_dict())
            return
        if path.startswith("/api/"):
            self._send_json(
                404,
                {
                    "schema": "StudioError@1",
                    "code": "API_ROUTE_NOT_FOUND",
                    "detail": "The requested Studio API route is not defined.",
                },
            )
            return
        if not self._static_dir.is_dir():
            self._send_json(
                404,
                {
                    "schema": "StudioError@1",
                    "code": "FRONTEND_NOT_BUILT",
                    "detail": "Run npm run dev or npm run build from apps/archflow-studio.",
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path.startswith("/api/"):
            self._send_json(
                501,
                {
                    "schema": "StudioError@1",
                    "code": "WRITE_PATH_NOT_IMPLEMENTED",
                    "detail": "The first Studio slice is read-only.",
                    "canonicalWriteAuthority": False,
                },
            )
            return
        self.send_error(405, "Method Not Allowed")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[studio] {self.address_string()} {format % args}")

    def _send_json(self, status: int, payload: JsonObject) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_server(
    *,
    host: str,
    port: int,
    static_dir: Path,
    kernel: ArchFlowKernelFacade | None = None,
) -> ThreadingHTTPServer:
    active_kernel = kernel or ArchFlowKernelFacade()
    handler = partial(
        StudioRequestHandler,
        kernel=active_kernel,
        static_dir=static_dir.resolve(),
    )
    return ThreadingHTTPServer((host, port), handler)


def run(*, host: str, port: int, static_dir: Path) -> None:
    server = create_server(host=host, port=port, static_dir=static_dir)
    print(f"ArchFlow Studio gateway: http://{host}:{server.server_port}")
    print("Mode: read-only preview; canonical write authority: false")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

