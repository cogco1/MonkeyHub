"""Conditional reads of the decision tree's derived views (ADR-008 phase 0a).

The tree's surfaces poll views that are re-derived from hundreds of retained
records. A view read under an unchanged ``ReadToken`` is the same view, so
each listed route gets an entity tag built from the binding's token, the path,
the query and the in-process versions the route also reads; ``If-None-Match``
answers 304, and a 200 read under a stable token is kept in the binding's memo
and answered again byte for byte.

Only an exact GET path listed here is touched, and only its 200 answer: every
other method, path and status passes through unchanged. A path is listed only
when its answer depends on the project's files, the query and the listed
versions and on nothing else (GH-363 audit). Git's racy rule extends to the
tag: a token that is not stable neither answers 304 nor is remembered, and the
tag it gives is marked, so it can never match a later stable token that
happens to carry the same digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from urllib.parse import parse_qsl

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import State
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..application.binding import ProjectBinding, ReadToken, bound_project

# The in-process state a listed route reads beside the project's files.
VERSIONS: dict[str, Callable[[State], Any]] = {
    # JobRegistry publishes an event on every job transition it makes.
    "events": lambda state: state.events.sequence,
    "render_jobs": lambda state: state.render_jobs.version,
}

# Exact GET path -> the versions its answer depends on.
CONDITIONAL_READS: dict[str, tuple[str, ...]] = {
    "/api/design-history": (),
    "/api/worktrees": ("events", "render_jobs"),
    "/api/artifacts": (),
    "/api/documents": (),
    "/api/working-source": (),
    "/api/board": (),
    "/api/render/jobs": ("render_jobs",),
}

NOT_CACHED = b"no-cache"


def entity_tag(token: ReadToken, path: str, query: list[tuple[str, str]], versions: list[tuple[str, Any]]) -> str:
    """The strong tag of one view: the token, the path, the query and the versions."""

    material = [token.epoch, token.serial, token.fingerprint, path, query, versions]
    if not token.stable:
        material.append("unstable")
    digest = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f'"{digest[:32]}"'


def _matches(header: str, tag: str) -> bool:
    """Whether an ``If-None-Match`` list names ``tag``; ``W/`` is compared weakly."""

    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == tag:
            return True
    return False


def _header(scope: Scope, name: bytes) -> str:
    return ",".join(value.decode("latin-1") for key, value in scope.get("headers", ()) if key.lower() == name)


def _token(state: State) -> tuple[ProjectBinding, ReadToken]:
    binding = bound_project(state)
    return binding, binding.read_token()


async def _answer(send: Send, status: int, tag: str, body: bytes = b"", media_type: str | None = None) -> None:
    headers = [(b"etag", tag.encode("latin-1")), (b"cache-control", NOT_CACHED)]
    if status != 304:
        headers = [(b"content-length", str(len(body)).encode("latin-1")),
                   *([(b"content-type", media_type.encode("latin-1"))] if media_type else []), *headers]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body if status != 304 else b""})


class ConditionalReads:
    """ETag, ``If-None-Match`` and a per-binding memo for the listed GET views."""

    def __init__(self, app: ASGIApp, *, state: State) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        listed = (
            CONDITIONAL_READS.get(scope.get("path", ""))
            if scope["type"] == "http" and scope.get("method") == "GET" else None
        )
        if listed is None:
            await self.app(scope, receive, send)
            return
        try:
            binding, token = await run_in_threadpool(_token, self.state)
            versions = [(name, VERSIONS[name](self.state)) for name in listed]
        except Exception:  # noqa: BLE001 - the route states a binding failure itself
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        query = sorted(parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True))
        tag = entity_tag(token, path, query, versions)
        key = ("conditional-read", tag)
        if token.stable:
            if _matches(_header(scope, b"if-none-match"), tag):
                await _answer(send, 304, tag)
                return
            kept = binding.memo_get(token, key)
            if kept is not None:
                status, body, media_type = kept
                await _answer(send, status, tag, body, media_type)
                return

        answered: dict[str, Any] = {"chunks": None}

        async def tagged(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 200:
                headers = [(name, value) for name, value in message.get("headers", ())
                           if name.lower() not in (b"etag", b"cache-control")]
                answered["media_type"] = next(
                    (value.decode("latin-1") for name, value in headers if name.lower() == b"content-type"), None,
                )
                answered["chunks"] = [] if token.stable else None
                message = {**message, "headers": [*headers, (b"etag", tag.encode("latin-1")),
                                                   (b"cache-control", NOT_CACHED)]}
            elif message["type"] == "http.response.body" and answered["chunks"] is not None:
                answered["chunks"].append(message.get("body", b""))
                if not message.get("more_body", False):
                    binding.memo_put(token, key, (200, b"".join(answered["chunks"]), answered["media_type"]))
                    answered["chunks"] = None
            await send(message)

        await self.app(scope, receive, tagged)
