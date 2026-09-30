"""The Hub's two connections to a project's worker: one request at a time, and one held event stream.

``request_http`` sends exactly one request and returns its answer, error
statuses included, and never follows a redirect. ``_WorkerEvents`` holds the
Hub's one attachment to a worker's own event stream and hands each event to the
manager that relays it.
"""

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import json
import socket
import threading
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, ProxyHandler, Request, build_opener

from ..chat.transport import _NoRedirect

if TYPE_CHECKING:
    from .manager import ProjectRuntimeManager


# How long the Hub waits before it attaches to a worker's event stream again
# after that stream ended or was refused: this first, then doubled for each
# further attachment that carried nothing, up to the cap; back to this after one that did.
_WORKER_EVENTS_RETRY_S = 0.5
_WORKER_EVENTS_RETRY_MAX_S = 30.0


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self):
        try:
            value = json.loads(self.body)
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeError):
            return {}


def _connect_then_time(address, timeout, source_address=None) -> socket.socket:
    """Connect to a worker at once; the request's timeout then bounds the exchange.

    A timed connect waits in select(), which Windows wakes a timer tick (about
    15 ms) late even when the local worker accepted at once.
    """
    connection = socket.create_connection(address, None, source_address)
    connection.settimeout(timeout)
    return connection


class _WorkerConnection(HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_then_time


class _WorkerHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_WorkerConnection, req)


# One opener for every worker request: building one makes an HTTPS handler whose
# default context reads the system certificate store, about 20 ms on Windows.
_WORKER_OPENER = build_opener(ProxyHandler({}), _NoRedirect(), _WorkerHandler())


def request_http(base: str, path: str, method="GET", body: bytes | None = None,
                 headers: dict[str, str] | None = None, *, timeout: float = 10) -> HttpResult:
    """Exactly one request, including error responses. Never follows a redirect."""
    request = Request(base.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        response = _WORKER_OPENER.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return HttpResult(response.status, response.read(), {
            name: value for name, value in response.headers.items()
            if name.lower() in {"content-type", "content-disposition", "etag", "cache-control", "x-monkey-index"}
        })


class _WorkerEvents:
    """The Hub's one attachment to a project worker's event stream (#366).

    It forwards what a browser follows on ``/api/runtime/events``, so each Hub
    page holds one stream however many projects and surfaces it shows:
    ``index.committed`` as an index hint, every other event as the Studio's own,
    with the worker stream it was numbered on (``stream``): a restarted worker
    numbers from 1 again, and only ``<stream>:<seq>`` tells its events apart
    from the last one's. When the stream may have lost something - the first
    attachment to this worker, or a ``stream.reset`` (the worker restarted, or
    its buffer overflowed) - it also sends a hint without a revision, so
    clients read the index again; a reattachment that resumes sends none. A
    hint is never the data and never required: clients also read on their own
    stream's snapshot and on focus.

    A stream that ends or is refused is attached again after
    ``_WORKER_EVENTS_RETRY_S`` (0.5 s), then after a delay that doubles while
    attachments carry nothing (1, 2, 4 s ... up to ``_WORKER_EVENTS_RETRY_MAX_S``);
    an attachment that carried an event starts the delays from 0.5 s again.
    """

    def __init__(self, manager: "ProjectRuntimeManager", runtime_id: str, url: str) -> None:
        self.manager, self.runtime_id, self.url = manager, runtime_id, url
        self._stopped = threading.Event()
        self._socket: socket.socket | None = None
        self._last_id = ""
        self._attached = False
        self._received = False
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"hub-worker-events-{runtime_id[:8]}")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stopped.set()
        stream = self._socket
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    @property
    def alive(self) -> bool:
        return self.thread.is_alive() and not self._stopped.is_set()

    def _run(self) -> None:
        address = urlsplit(self.url)
        delay = _WORKER_EVENTS_RETRY_S
        while not self._stopped.is_set():
            connection = HTTPConnection(address.hostname, address.port, timeout=10)
            self._received = False
            try:
                connection.connect()
                self._socket = stream = connection.sock
                if self._stopped.is_set():
                    return
                connection.request("GET", "/api/events", headers={"Last-Event-ID": self._last_id,
                                                                   "Accept": "text/event-stream"})
                response = connection.getresponse()
                if response.status == 200:
                    # A held stream is quiet between events; only stop() or the worker ends it.
                    # (A response that closes the connection has taken the socket from it.)
                    stream.settimeout(None)
                    if not self._attached:
                        # This worker's history before now was never relayed: clients read the index again.
                        self._attached = True
                        self.manager.index_hint(self.runtime_id, None)
                    self._read(response)
            except (OSError, HTTPException, ValueError):
                pass
            finally:
                self._socket = None
                connection.close()
            if self._received:
                delay = _WORKER_EVENTS_RETRY_S
            self._stopped.wait(delay)
            delay = min(delay * 2, _WORKER_EVENTS_RETRY_MAX_S)

    def _read(self, response) -> None:
        event, data, event_id = "", [], None
        while not self._stopped.is_set():
            line = response.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("event:"):
                event = text[6:].strip()
            elif text.startswith("data:"):
                data.append(text[5:].strip())
            elif text.startswith("id:"):
                event_id = text[3:].strip()
            elif text == "":
                if data:
                    self._received = True
                    self._dispatch(event, "\n".join(data), event_id)
                if event_id is not None:
                    self._last_id = event_id
                event, data, event_id = "", [], None

    def _dispatch(self, event: str, data: str, event_id: str | None) -> None:
        try:
            body = json.loads(data)
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        if event == "index.committed":
            self.manager.index_hint(self.runtime_id, {"epoch": body.get("epoch"), "revision": body.get("revision"),
                                                      "domains": body.get("domains") or []})
        elif event == "stream.reset":
            self.manager.index_hint(self.runtime_id, None)
        elif event:
            # ``<stream>:<seq>``: the worker process that numbered it.
            stream = (event_id or "").rpartition(":")[0] or None
            self.manager.studio_event(self.runtime_id, stream, body)
