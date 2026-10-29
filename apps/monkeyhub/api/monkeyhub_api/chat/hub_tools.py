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
from . import transport
from ..models import HubFailure

TOOL_NAMES = ("memory_note", "chat_schedule", "chat_find", "chat_read", "chat_send")
#: Their answers carry only the Hub's own bookkeeping; chat_find and chat_read carry other conversations' words.
HUB_OWN = frozenset({"memory_note", "chat_schedule", "chat_send"})


# ---- the MCP side ------------------------------------------------------------

def tool_definitions() -> list[dict]:
    # One sentence each and bare types: this listing is read before each turn. Every bound and rule is the route's
    # own (notes, wakeups, relay) and comes back in its refusal; unknown arguments are refused by _fields.
    return [
        {"name": "memory_note", "description": "Keep or remove a note the architect asked you to remember; later turns "
         "read it. scope project or all; remove takes id.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["add", "remove"]},
             "text": {"type": "string"},
             "scope": {"type": "string", "enum": ["project", "all"]},
             "id": {"type": "string"},
         }, "required": ["action"]}},
        {"name": "chat_schedule", "description": "Wake this conversation later with prompt: inMinutes or at (local ISO) "
         "once, everyMinutes or dailyAt (HH:MM, weekdays 0=Mon) repeating, or afterJob {path}; list; cancel id.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["add", "list", "cancel"]},
             "prompt": {"type": "string"},
             "inMinutes": {"type": "number"},
             "at": {"type": "string"},
             "everyMinutes": {"type": "integer"},
             "dailyAt": {"type": "string"},
             "weekdays": {"type": "array", "items": {"type": "integer"}},
             "afterJob": {"type": "object", "properties": {"path": {"type": "string"}}},
             "id": {"type": "string"},
         }, "required": ["action"]}},
        {"name": "chat_find", "description": "Find conversations by words in their titles or messages, else the newest; "
         "gives chatIds.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string"}, "archived": {"type": "boolean"},
             "limit": {"type": "integer"},
         }}},
        {"name": "chat_read", "description": "Read another conversation's last messages.",
         "inputSchema": {"type": "object", "properties": {
             "chatId": {"type": "string"}, "limit": {"type": "integer"},
         }, "required": ["chatId"]}},
        {"name": "chat_send", "description": "Send text to another conversation; it answers there.",
         "inputSchema": {"type": "object", "properties": {
             "chatId": {"type": "string"}, "text": {"type": "string"},
         }, "required": ["chatId", "text"]}},
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
