"""One call from the chat's tools to a bound local service: this Hub or the chat's own Studio.

Every call goes through one opener that follows no proxy and no redirect, to a
loopback http address only, carries the running turn's trace headers and waits
no longer than its caller's own limit. Independent calls can be made at once,
and a registered page comes back as a bounded PNG.
"""

from __future__ import annotations

import base64
from contextvars import ContextVar, copy_context
import json
import re
import threading
import time
from typing import Mapping
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import providers

from ..models import HubFailure


_trace_headers = ContextVar("hub_tool_trace_headers", default={})


_PAGE_IMAGE_MAX_EDGE = 2048
_PAGE_IMAGE_MAX_BYTES = 4 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The bound service redirected the request.")


# One opener for every call to a bound service (#363): building one makes an
# HTTPS handler whose default context reads the system certificate store,
# about 20 ms of CPU per call on Windows. ``_url`` lets only http through.
_SERVICE_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def _url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.username or url.password or url.query or url.fragment:
        raise HubFailure(422, "CHAT_SERVICE_INVALID", "Only the bound local application can be called.")
    return f"http://{url.netloc}"


def _request_json(base: str, path: str, method: str = "GET", body=None, timeout: float = 180, *, headers=None, png: bool = False):
    """One call to a bound service, with the caller's own time limit on it.

    ``timeout`` is what makes a deadline real: a call that has run out of time
    stops waiting on the socket rather than holding the conversation open past
    the limit the caller was told about.
    """

    if timeout <= 0:
        # A budget that is already spent buys nothing: the call is not made,
        # rather than made with a small amount of time granted to it here.
        raise TimeoutError(f"no time left to call {method} {path}")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    # Preserve existing escapes and delimiters, while allowing a natural-language
    # capability query to contain Unicode without failing in urllib's ASCII URL.
    request = Request(_url(base) + quote(path, safe="/%?=&:+,;@!$'()*~-._"), data=data, method=method, headers={
        "Content-Type": "application/json", **_trace_headers.get(), **(headers or {}),
    })
    try:
        with _SERVICE_OPENER.open(request, timeout=timeout) as response:
            if png:
                return _page_image(response)
            return json.load(response)
    except HTTPError as exc:
        code = "CHAT_TOOL_FAILED"
        try:
            payload = json.load(exc)
            detail = payload.get("detail", "The application refused the request.")
            reported = payload.get("code")
            if isinstance(reported, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reported):
                code = reported
        except (ValueError, AttributeError):
            detail = "The application refused the request."
        raise HubFailure(exc.code, code, providers._redact(str(detail))[:1200]) from exc


def _page_image(response) -> dict:
    """Decode a bounded PNG from the registered-page owner, never a file path."""
    from io import BytesIO
    from PIL import Image

    if response.headers.get("Content-Type", "").split(";")[0].strip().lower() != "image/png":
        raise HubFailure(502, "CHAT_IMAGE_INVALID", "The registered page export did not return image/png.")
    data = response.read(_PAGE_IMAGE_MAX_BYTES + 1)
    if len(data) > _PAGE_IMAGE_MAX_BYTES:
        raise HubFailure(413, "CHAT_IMAGE_TOO_LARGE", "The PNG exceeds 4 MiB. Read the same page with a smaller maxEdge.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or max(image.size) > _PAGE_IMAGE_MAX_EDGE:
                raise ValueError("not a bounded PNG")
            image.load()
            width, height = image.size
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HubFailure(502, "CHAT_IMAGE_INVALID", "The page export must be a valid PNG with neither edge above 2048 pixels.") from exc
    return {"mimeType": "image/png", "width": width, "height": height, "data": base64.b64encode(data).decode("ascii")}


def _together(calls: Mapping[str, tuple], timeout: float, *, allow_partial: bool = False) -> dict:
    """Ask for several independent things at once, and wait for all of them.

    Only calls that do not depend on each other are passed here, and the threads
    live and die inside this function: there is no worker layer, and nothing is
    queued across requests. They are daemon threads holding a result local to
    this call, so a service that has stopped answering is abandoned at the
    deadline and cannot go on holding this process open — neither this wait nor
    the interpreter's own exit is left waiting on a socket nobody wants any
    more. A refusal is raised in the order the caller listed the calls, so the
    answer a client gets does not depend on which reply happened to lose the
    race.

    Completion readbacks may keep successful siblings alongside expected read
    failures. Binding checks retain the default all-or-nothing behavior, and
    unexpected exceptions always propagate.
    """

    ends = time.monotonic() + timeout
    answers: dict[str, object] = {}
    keep = threading.Lock()

    def collect(name: str, call: tuple, context) -> None:
        try:
            answer = context.run(_request_json, *call, timeout=timeout)
        except BaseException as cause:  # noqa: BLE001 - re-raised below, in order
            answer = cause
        with keep:
            answers[name] = answer

    threads = [(name, threading.Thread(target=collect, args=(name, call, copy_context()),
                                       daemon=True, name="hub-together"))
               for name, call in calls.items()]
    for _, thread in threads:
        thread.start()
    for _, thread in threads:
        # Waiting is bounded by the same deadline the calls are: a thread that
        # outlives it is abandoned rather than waited on.
        thread.join(timeout=max(0.0, ends - time.monotonic()))
    ordered = []
    for name, _ in threads:
        with keep:
            answered, answer = name in answers, answers.get(name)
        ordered.append((name, answer if answered else
                        TimeoutError(f"no answer for {name} within {timeout:.0f}s")))
    for _, answer in ordered:
        if isinstance(answer, BaseException) and (
            not allow_partial or not isinstance(answer, (HubFailure, OSError, TimeoutError))
        ):
            raise answer
    return dict(ordered)


def _reason(cause: BaseException) -> str:
    """One sentence about a failed read, in the words it came with."""

    if isinstance(cause, HubFailure):
        return providers._redact(str(cause.error.detail))[:400]
    return providers._redact(f"{type(cause).__name__}: {cause}")[:400]
