"""Which of a Codex agent's permission requests a person can allow once for a whole project (A4).

An ACP permission request names a tool call and the adapter's own options (allow once, allow always, reject), and
the adapter says which approval it is in the request's own ``_meta`` (the locked codex-acp 1.11.0): ``permission.title``
is "Run command?" for a command and "Allow network access?" for going online (both are tool calls of kind ``execute``),
and ``is_mcp_tool_approval`` marks an MCP tool call, whose server is only in the tool call it correlates with
(``_meta.is_mcp_tool_call`` and ``rawInput.server``, announced earlier). Three kinds of request can be allowed for a
project: a command the agent starts, going online, and a call to MonkeyHub's own tools (an MCP call to the
``monkeyhub`` server, which the Hub gates and attributes). Everything else is asked every time, including every
request whose kind is not told by that metadata: an edit above all, since the bound project is read-only to every
agent's file tools (ADR-012), more sandbox permissions, a URL an MCP server asks to open, a tool of any other MCP
server or one whose server is not known, none of which a grant for commands may quietly cover.
``settings.tool_grants`` keeps the answers.

The Hub answers from a grant with the adapter's own "allow once" option and never with its "allow always": the
adapter would then remember it for the session and the Hub could no longer stop it when the person withdraws the
grant. The adapter's option ids are never assumed; options are found by their ``kind``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

# The option a person's "always allow in this project" gets in the card; the adapter never sees it.
HUB_PROJECT = "hub-project"
# What the locked adapter titles the two approvals that start something (its request-level _meta.permission.title).
COMMAND_TITLE = "Run command?"
NETWORK_TITLE = "Allow network access?"
# The MCP server the Hub starts for a conversation; its tools are the Hub's own.
HUB_SERVER = "monkeyhub"
_PREVIEW = 160


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mcp_call(request: Mapping[str, Any], calls: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The MCP tool call this request is about: the one announced under its id, else the request's own."""

    tool = _mapping(request.get("toolCall"))
    known = _mapping(calls).get(str(tool.get("toolCallId") or ""))
    for source in (known, tool):
        if _mapping(_mapping(source).get("_meta")).get("is_mcp_tool_call") is True:
            return _mapping(source)
    return None


def _mcp_server(request: Mapping[str, Any], calls: Mapping[str, Any] | None) -> str | None:
    call = _mcp_call(request, calls)
    if call is not None:
        server = _mapping(call.get("rawInput")).get("server")
        return server if isinstance(server, str) else None
    # A standalone approval, asked with no tool call before it, names its server itself.
    server = _mapping(_mapping(request.get("toolCall")).get("rawInput")).get("serverName")
    return server if isinstance(server, str) else None


def action_of(request: Mapping[str, Any], calls: Mapping[str, Any] | None = None) -> str | None:
    """``run``, ``network`` or ``hub`` when this request is one a person can allow for a project, else None.

    ``calls`` are the tool calls the adapter has announced in this conversation, by id.
    """

    tool = request.get("toolCall")
    if not isinstance(tool, Mapping):
        return None
    meta = _mapping(request.get("_meta"))
    if meta.get("is_mcp_tool_approval") is True or _mapping(tool.get("_meta")).get("is_mcp_tool_call") is True:
        return "hub" if _mcp_server(request, calls) == HUB_SERVER else None
    if tool.get("kind") != "execute" or _mapping(tool.get("rawInput")).get("additionalPermissions"):
        return None
    title = _mapping(meta.get("permission")).get("title")
    if title == NETWORK_TITLE:
        return "network"
    if title == COMMAND_TITLE:
        return "run"
    return None


def call_title(request: Mapping[str, Any], calls: Mapping[str, Any] | None = None) -> str | None:
    """What the request calls itself, else what the tool call it correlates with was called."""

    tool = _mapping(request.get("toolCall"))
    known = _mapping(_mapping(calls).get(str(tool.get("toolCallId") or "")))
    title = tool.get("title") or known.get("title")
    return str(title) if title else None


def preview(request: Mapping[str, Any], calls: Mapping[str, Any] | None = None) -> str:
    """What the request is about in one short line: the command it runs or the address it fetches, if it says."""

    tool = _mapping(request.get("toolCall"))
    raw = tool.get("rawInput") or _mapping(_mapping(calls).get(str(tool.get("toolCallId") or ""))).get("rawInput")
    found: Any = None
    if isinstance(raw, Mapping):
        found = next((raw[key] for key in ("command", "cmd", "url", "uri") if raw.get(key)), None)
        if found is None and raw.get("tool"):
            arguments = raw.get("arguments") if isinstance(raw.get("arguments"), Mapping) else {}
            found = " ".join(str(part) for part in (raw["tool"], arguments.get("method"), arguments.get("path")) if part)
    elif isinstance(raw, str):
        found = raw
    if isinstance(found, Sequence) and not isinstance(found, str):
        found = " ".join(str(part) for part in found)
    text = re.sub(r"\s+", " ", str(found or "")).strip()
    return text if len(text) <= _PREVIEW else text[:_PREVIEW - 1] + "…"


def option_id(options: Sequence[Mapping[str, Any]], *kinds: str) -> str | None:
    """The id of the adapter's first option of one of these kinds, or None."""

    return next((str(option["optionId"]) for option in options if option.get("kind") in kinds and option.get("optionId")), None)
