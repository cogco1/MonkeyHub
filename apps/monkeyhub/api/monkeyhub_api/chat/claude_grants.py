"""A Claude turn's permission questions, asked in the conversation when it runs at the "ask" level (gap 4).

At that level the Claude CLI is started in its manual mode with ``--permission-prompt-tool stdio``: a tool it
may not use unasked (an edit, a command, an MCP tool of another server) becomes a ``control_request`` of subtype
``can_use_tool`` on its stdout, and it waits for the ``control_response`` the Hub writes to its stdin. The Hub
shows the question as a permission card with the same three answers a Codex question has; "always" is kept in
``settings.tool_grants`` for this project or folder, by kind:

- ``run``: Bash or PowerShell; ``network``: WebFetch or WebSearch; ``hub``: MonkeyHub's own tools;
- ``edit``: a file tool, and only in a plain folder, which the conversation writes (a project is read-only to
  every agent's file tools, ADR-012);
- an MCP tool of any other server is asked every time.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

_RUN = frozenset({"Bash", "PowerShell"})
_NETWORK = frozenset({"WebFetch", "WebSearch"})
_EDIT = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_PREVIEW = 160


def action_of(tool: str, *, folder: bool) -> str | None:
    """The kind of request an "always" answer may cover, or None when it is asked every time."""
    if tool in _RUN:
        return "run"
    if tool in _NETWORK:
        return "network"
    if tool in _EDIT:
        return "edit" if folder else None
    if tool.startswith("mcp__monkeyhub__"):
        return "hub"
    return None


def preview(tool: str, arguments: Mapping[str, Any]) -> str:
    """What the card says the tool would do: its command, file, address or query, else its arguments, cut short."""
    for key in ("command", "file_path", "notebook_path", "url", "query", "path", "pattern"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            break
    else:
        value = json.dumps(arguments, ensure_ascii=False, default=str) if arguments else ""
    value = " ".join(value.split())
    return value if len(value) <= _PREVIEW else value[:_PREVIEW - 1] + "…"


def response(request_id: str, arguments: Mapping[str, Any], allow: bool, message: str | None = None) -> str:
    """The stdin line that answers one can_use_tool request, as the Agent SDK writes it."""
    answer = ({"behavior": "allow", "updatedInput": dict(arguments)} if allow
              else {"behavior": "deny", "message": message or "The person did not allow this."})
    return json.dumps({"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": answer}}, ensure_ascii=False) + "\n"
