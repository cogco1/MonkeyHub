"""Restricted member ingress. Iroh binds peer identities before forwarding bytes.

This server has no Hub routes and no project writer. Data streams only reach
existing permitted Runtime APIs. Enrollment has a separate protocol and route.
"""
from __future__ import annotations
import asyncio
import socket
import secrets
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from project_runtime.authentication import request_action
from ..models import HubFailure
from ..settings.team import digest


class Redemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=20, max_length=256)
    actorId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
    token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class MemberGateway:
    def __init__(self, service, project_id):
        self.service, self.project_id = service, project_id
        self.peers = {}
        self.lock = threading.Lock()
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route("/join", self.redeem, methods=["POST"])
        self.app.add_api_route("/{path:path}", self.forward, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
        self.app.add_exception_handler(HubFailure, self.error)
        self.app.add_exception_handler(RequestValidationError, self.validation_error)
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", access_log=False))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.socket]), daemon=True, name="team-member-entry")
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise HubFailure(503, "MEMBER_ENTRY_UNAVAILABLE", "The project connection could not start.")
            time.sleep(.01)

    async def validation_error(self, request, exc):
        return JSONResponse({"code": "JOIN_REQUEST_INVALID", "detail": "Check the invitation and device identity."}, status_code=422)

    async def error(self, request, exc):
        return JSONResponse(exc.error.model_dump(), status_code=exc.status)

    def peer_changed(self, event):
        with self.lock:
            if event["event"] == "peer":
                self.peers[event["port"]] = event
            else:
                self.peers.pop(event["port"], None)

    def peer(self, request, *, enrollment):
        with self.lock:
            peer = self.peers.get(request.client.port) if request.client else None
        if not peer or peer["enrollment"] != enrollment:
            raise HubFailure(403, "PEER_REQUIRED", "An admitted device connection is required.")
        return peer["peer"]

    async def redeem(self, request: Request, payload: Redemption):
        peer = self.peer(request, enrollment=True)
        member = self.service.settings.redeem(self.project_id, payload.code, peer, payload.actorId, payload.name, payload.token)
        self.service.admission(self.project_id)
        row = self.service.settings.project(self.project_id)
        return {"projectId": self.project_id, "role": member["role"], "ownerActorId": row["ownerActorId"]}

    async def forward(self, request: Request, path: str):
        peer = self.peer(request, enrollment=False)
        row = self.service.settings.project(self.project_id)
        bearer = request.headers.get("authorization", "")
        member = next((m for m in row["members"].values() if m["nodeId"] == peer and m["role"] != "revoked"), None)
        if not member or not bearer.startswith("Bearer ") or not secrets.compare_digest(digest(bearer[7:]), member["tokenHash"]):
            raise HubFailure(401, "MEMBER_REVOKED", "This device is not an active member.")
        route = "/" + path
        action = request_action(request.method, route, shared_project=True)
        if route not in {"/api/health", "/api/protocol"} and action is None:
            raise HubFailure(403, "MEMBER_PATH_FORBIDDEN", "This connection does not expose that API.")
        allowed = {"read"} | ({"propose"} if member["role"] in {"designer", "moderator"} else set()) | ({"accept"} if member["role"] == "moderator" else set())
        if action is not None and action not in allowed:
            raise HubFailure(403, "ACTION_FORBIDDEN", "Your team role does not grant this action.")
        status = self.service.applications.status("monkeyarch", project_dir=row["projectDir"])
        if not status.apiUrl:
            raise HubFailure(503, "OWNER_OFFLINE", "The project's Runtime is not ready.")
        if route == "/api/health":
            # The local worker health includes paths and process identity for its
            # Hub supervisor. Those machine details do not cross member ingress.
            return JSONResponse({"status": "ok", "service": "archflow-studio-api", "projectBound": True})
        url = status.apiUrl.rstrip("/") + route
        if request.url.query:
            url += "?" + request.url.query
        client = httpx.AsyncClient(trust_env=False, timeout=None, follow_redirects=False)
        try:
            upstream = await client.send(client.build_request(request.method, url, content=request.stream(),
                headers={key: value for key, value in request.headers.items() if key.lower() in {"authorization", "content-type", "last-event-id", "if-none-match"}}), stream=True)
        except (httpx.HTTPError, OSError):
            await client.aclose()
            raise HubFailure(503, "OWNER_OFFLINE", "The project's Runtime is not ready.") from None

        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    # Revocation affects an already connected event stream too.
                    current = self.service.settings.project(self.project_id)["members"].get(member["actorId"])
                    if not current or current["role"] == "revoked":
                        break
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(stream(), status_code=upstream.status_code,
            headers={key: value for key, value in upstream.headers.items() if key.lower() in {"content-type", "cache-control", "etag", "content-disposition"}})

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
