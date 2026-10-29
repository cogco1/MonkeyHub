"""The Hub's own conversation tools: memory, wake-ups and other conversations.

``tool_definitions`` and ``call`` run in the stdio MCP connection a
conversation's CLI starts (``mcp_server``) and reach the Hub over HTTP with the
conversation's presentation token; ``hub_routes`` is the Hub side that checks
it. Nothing here imports the web framework, so the connection starts light.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from .. import projects
from . import notes, relay, transport
from ..models import HubFailure

TOOL_NAMES = ("memory_note", "chat_schedule", "chat_find", "chat_read", "chat_send")
#: Their answers carry only the Hub's own bookkeeping; chat_find and chat_read carry other conversations' words.
HUB_OWN = frozenset({"memory_note", "chat_schedule", "chat_send"})


# ---- the MCP side ------------------------------------------------------------

def tool_definitions() -> list[dict]:
    return [
        {"name": "memory_note", "description": (
            "Keep a short note the architect asked you to remember, or remove one. The architect's instructions and these "
            "notes are given to every later turn: of every conversation (scope all) or of this project (scope project, "
            "the default). The architect sees each note in the Hub and can delete it. Keep only what the architect asked "
            "you to remember or confirmed, one fact per note, under 500 characters. remove takes the id shown beside a note."),
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["add", "remove"]},
             "text": {"type": "string", "minLength": 1, "maxLength": notes.NOTE_LIMIT},
             "scope": {"type": "string", "enum": ["project", "all"], "default": "project"},
             "id": {"type": "string", "minLength": 8},
         }, "required": ["action"], "additionalProperties": False}},
        {"name": "chat_schedule", "description": (
            "Wake this conversation later so it continues by itself. Timing, exactly one: inMinutes or at (an ISO "
            "date-time, local time without an offset) for once; everyMinutes (at least 5) or dailyAt (HH:MM local, "
            "optional weekdays 0=Monday..6=Sunday) to repeat; afterJob {path} to wake when a job of this project ends, "
            "path being its read such as /api/skills/runs/<runId>, /api/render/jobs/<jobId>, /api/jobs/<jobId> or "
            "/api/exports/<exportId> (gives up after 4 hours). When due, the Hub sends prompt to this conversation as a "
            "message no person typed and you run one turn; a running turn finishes first. Use it when the architect asks "
            "for a later or repeated check, or to pick up after a long job instead of waiting in this turn. The architect "
            "sees and can cancel every wake-up. action list shows this conversation's; cancel takes an id."),
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["add", "list", "cancel"]},
             "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
             "inMinutes": {"type": "number", "minimum": 1},
             "at": {"type": "string"},
             "everyMinutes": {"type": "integer", "minimum": 5},
             "dailyAt": {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
             "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}, "maxItems": 7},
             "afterJob": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                          "additionalProperties": False},
             "id": {"type": "string", "minLength": 8},
         }, "required": ["action"], "additionalProperties": False}},
        {"name": "chat_find", "description": (
            "Find this Hub's conversations. With query: those whose title or messages contain every word, newest first, "
            "each with up to three matching snippets. Without: the newest conversations. archived true searches the "
            "archived ones instead. Use the chatId with chat_read or chat_send."),
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "maxLength": 200},
             "archived": {"type": "boolean", "default": False},
             "limit": {"type": "integer", "minimum": 1, "maximum": relay.SEARCH_LIMIT, "default": 10},
         }, "additionalProperties": False}},
        {"name": "chat_read", "description": "Read the last messages of another conversation by chatId: what the architect "
         "and its Agent said, without tool steps, each cut at 2000 characters.",
         "inputSchema": {"type": "object", "properties": {
             "chatId": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
         }, "required": ["chatId"], "additionalProperties": False}},
        {"name": "chat_send", "description": (
            "Send a message to another conversation of this Hub (a chatId from chat_find), as a colleague's request. It "
            "arrives there marked as coming from this conversation and starts a turn there, or joins the one running. "
            "Its answer stays in that conversation: read it later with chat_read, ask it to chat_send back, or wake "
            "yourself with chat_schedule to check. A chain of such messages that no person started stops after "
            f"{relay.RELAY_DEPTH}."),
         "inputSchema": {"type": "object", "properties": {
             "chatId": {"type": "string"}, "text": {"type": "string", "minLength": 1, "maxLength": 8000},
         }, "required": ["chatId", "text"], "additionalProperties": False}},
    ]


def _fields(arguments, allowed: set[str], name: str) -> dict:
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise HubFailure(422, "CHAT_TOOL_INVALID", f"{name} takes only {', '.join(sorted(allowed))}.")
    return arguments


def call(hub: str, chat_id: str, name: str, arguments) -> dict:
    """One tool call, answered by the Hub for this conversation (``chat_id``)."""

    token = os.environ.get("MONKEYHUB_PRESENTATION_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"}
    chat = projects._identifier(chat_id)
    if name == "memory_note":
        body = _fields(arguments, {"action", "text", "scope", "id"}, name)
        return transport._request_json(hub, f"/api/chat/sessions/{chat}/notes", "POST", body, headers=headers)
    if name == "chat_schedule":
        body = dict(_fields(arguments, {"action", "prompt", "inMinutes", "at", "everyMinutes", "dailyAt", "weekdays",
                                        "afterJob", "id"}, name))
        action = body.pop("action", None)
        if action == "list":
            return {"wakeups": transport._request_json(hub, f"/api/chat/sessions/{chat}/wakeups")}
        if action == "cancel":
            if not isinstance(body.get("id"), str):
                raise HubFailure(422, "CHAT_TOOL_INVALID", "cancel takes the wake-up's id.")
            return transport._request_json(hub, f"/api/chat/sessions/{chat}/wakeups/{body['id']}/cancel", "POST", {},
                                           headers=headers)
        if action != "add":
            raise HubFailure(422, "CHAT_TOOL_INVALID", "action is add, list or cancel.")
        body.pop("id", None)
        return transport._request_json(hub, f"/api/chat/sessions/{chat}/wakeups", "POST", body, headers=headers)
    if name == "chat_find":
        body = _fields(arguments, {"query", "archived", "limit"}, name)
        query = {"q": str(body.get("query") or ""), "archived": "true" if body.get("archived") else "false",
                 "limit": int(body.get("limit") or 10)}
        return {"conversations": transport._request_json(hub, "/api/chat/search?" + urlencode(query))}
    if name == "chat_read":
        body = _fields(arguments, {"chatId", "limit"}, name)
        other = projects._identifier(str(body.get("chatId") or ""))
        return transport._request_json(hub, f"/api/chat/sessions/{other}/recent?limit={int(body.get('limit') or 10)}")
    if name == "chat_send":
        body = _fields(arguments, {"chatId", "text"}, name)
        other = projects._identifier(str(body.get("chatId") or ""))
        return transport._request_json(hub, f"/api/chat/sessions/{chat}/relay", "POST",
                                       {"to": other, "text": body.get("text")}, headers=headers)
    raise HubFailure(404, "CHAT_TOOL_UNKNOWN", f"{name} is not a Hub conversation tool.")
