"""The chat's own Studio: found, prepared and verified before a tool uses it.

A design tool prepares its bound project's runtime itself, one preparation per
project at a time, and every call then checks that the answering Studio is the
Hub's current service for exactly that project.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Mapping
from urllib.parse import urlencode

from .. import projects
from . import transport

from ..models import HubFailure


# One preparation per project at a time in this process. A call that finds a
# preparation under way waits for it and then reads the result, instead of
# opening or starting the project a second time.
_PREPARING = threading.Lock()
_PREPARATIONS: dict[str, threading.Lock] = {}
_PREPARE_POLL_S = 0.5


def _studio_row(apps) -> dict:
    return next((row for row in apps if row.get("appId") == "monkeyarch"), {})


def _studio_running(row: Mapping) -> bool:
    return row.get("state") == "running" and bool(row.get("apiUrl")) and bool(row.get("processId"))


def _prepare_studio(hub: str, session: Mapping, budget: float) -> dict:
    """Open the bound project's runtime and start its Studio, as the Hub page does.

    These are the Hub's own steps (open, read the attachment, start the service,
    wait until it runs), taken only for the chat's bound project, and all of
    them share one ``budget`` of seconds. A worker that needs recovery is
    refused with its own error and nothing is started: recovery stays an
    explicit act. Returns the running Studio's row for the checks that follow.
    """

    ends = time.monotonic() + budget

    def left() -> float:
        return ends - time.monotonic()

    def unready() -> HubFailure:
        return HubFailure(503, "CHAT_STUDIO_UNAVAILABLE",
                          "The project runtime did not become ready in time. Retry, or open MonkeyArch to see why.")

    def ask(*call, **options):
        if left() <= 0:
            raise unready()
        try:
            return transport._request_json(*call, **options, timeout=left())
        except OSError as exc:  # a timeout or a Hub that stopped answering
            raise unready() from exc

    query = urlencode({"projectDir": session["projectDir"]})
    with _PREPARING:
        held = _PREPARATIONS.setdefault(os.path.normcase(session["projectDir"]), threading.Lock())
    if not held.acquire(timeout=max(0.0, left())):
        raise unready()
    try:
        studio = _studio_row(ask(hub, f"/api/apps?{query}"))
        if _studio_running(studio):
            return studio
        opened = ask(hub, "/api/runtime/projects/open", "POST",
                     {"projectDir": session["projectDir"], "projectId": session["projectId"]})
        attached = next((row for row in ask(hub, "/api/runtime").get("projects", [])
                         if row.get("runtimeId") == opened.get("runtimeId")), None)
        if attached is None or session["projectId"] != attached.get("projectId") or opened.get("projectId") != session["projectId"]:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The project runtime is attached to a different project.")
        worker = next((row for row in attached.get("workers", []) if row.get("serviceId") == "studio"), {})
        if worker.get("state") == "crashed" or (worker.get("state") == "unavailable" and worker.get("processId")):
            error = worker.get("error") or {}
            raise HubFailure(409, error.get("code") or "WORKER_NEEDS_RECOVERY",
                             error.get("detail") or "The project service exited. Recover it to read saved results.")
        if not worker.get("healthy"):
            status = ask(hub, f"/api/apps/monkeyrender/start?{query}", "POST", {})
            while status.get("state") != "running" or not status.get("url"):
                if status.get("state") in {"error", "unavailable"}:
                    error = status.get("error") or {}
                    raise HubFailure(503, error.get("code") or "CHAT_STUDIO_UNAVAILABLE",
                                     error.get("detail") or f"The project service is {status.get('state')}.")
                if left() <= _PREPARE_POLL_S:
                    raise unready()
                time.sleep(_PREPARE_POLL_S)
                status = next((row for row in ask(hub, f"/api/apps?{query}") if row.get("appId") == "monkeyrender"), {})
        while not _studio_running(studio := _studio_row(ask(hub, f"/api/apps?{query}"))):
            if left() <= _PREPARE_POLL_S:
                raise unready()
            time.sleep(_PREPARE_POLL_S)
        return studio
    except HubFailure as failure:
        # Recovery, ports and settings are the user's: an agent cannot fix a
        # runtime that will not start, so it is told to say so, in the Hub's words.
        if failure.error.code in {"CHAT_STUDIO_UNAVAILABLE", "CHAT_PROJECT_MISMATCH"}:
            raise
        raise HubFailure(failure.status, failure.error.code,
                         f"{failure.error.detail} The project runtime could not start: tell the user this, "
                         "and do not change Hub settings or retry the same call.") from failure
    finally:
        held.release()


def _not_running(session: Mapping) -> HubFailure:
    """A tool call outside a turn, said as what it is: not started yet, or ended.

    Before its first user message a chat has no turn at all, and calling that
    "no longer running" sent a caller looking for a turn that had stopped.
    """
    if not any(row.get("role") == "user" for row in session.get("messages", ())):
        waiting = ("Publish the user's message with chat_present kind=user first; project tools then answer within that turn."
                   if session.get("sourceSessionId") else "Project tools answer once a user message starts a turn.")
        return HubFailure(409, "CHAT_NOT_RUNNING", f"This chat has not started a turn yet. {waiting}")
    return HubFailure(409, "CHAT_NOT_RUNNING", "This chat's turn has ended; project tools answer only while a turn runs.")


def _bound_studio(hub: str, chat_id: str | None, timeout: float = 180, *, project_id: str | None = None,
                  project_dir: str | None = None, deadline: float | None = None,
                  prepare: bool = True) -> tuple[str, dict]:
    """Resolve the chat's own Studio, then verify its process and project before use.

    With a ``deadline`` these checks share what is left of one budget instead of
    each starting ``timeout`` again; without one they keep the per-check limit
    the tool callers already have. ``prepare=False`` only finds a Studio that is
    already running and starts nothing.
    """

    def left() -> float:
        return timeout if deadline is None else deadline - time.monotonic()

    if chat_id is None:
        configured_path = project_dir if project_dir is not None else transport._request_json(hub, "/api/settings/apps", timeout=left()).get("projectDir")
        if not configured_path:
            raise HubFailure(409, "PROJECT_REQUIRED", "Choose a project before preparing its modeling workspace.")
        actual_id, actual_path = projects._project(configured_path)
        if project_id != actual_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The selected project changed before its workspace was prepared.")
        session = {"projectId": actual_id, "projectDir": actual_path}
    else:
        session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}", timeout=left())
        if session.get("status") != "running":
            raise _not_running(session)
        turn = next((row.get("id") for row in reversed(session.get("messages", [])) if row.get("role") == "user"), None)
        if turn:
            transport._trace_headers.set({"X-Monkey-Turn-Id": turn, "X-Monkey-Parent-Span-Id": f"hub:turn:{turn}"})
    if projects._project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
    first = transport._together({
        "apps": (hub, "/api/apps?" + urlencode({"projectDir": session["projectDir"]})),
        "hub_health": (hub, "/api/health"),
    }, left())
    studio = _studio_row(first["apps"])
    if not _studio_running(studio) and chat_id is not None and prepare:
        # A design tool prepares its own project rather than asking someone to
        # open a page; the checks below then apply to what it prepared.
        studio = _prepare_studio(hub, session, left())
    if not _studio_running(studio):
        raise HubFailure(409, "CHAT_STUDIO_UNAVAILABLE", "Open MonkeyArch for this project before using a design tool.")
    base = transport._url(studio["apiUrl"])
    second = transport._together({
        "health": (base, "/api/health"),
        "binding": (base, "/api/project"),
    }, left())
    health = second["health"]
    if health.get("processId") != studio["processId"] or health.get("sourceRevision") != first["hub_health"].get("sourceRevision"):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The responding Studio is not the Hub's current service.")
    binding = second["binding"]
    if binding.get("projectId") != session["projectId"] or str(Path(binding.get("projectDir", "")).resolve()) != session["projectDir"]:
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The running application belongs to a different project.")
    return base, session
