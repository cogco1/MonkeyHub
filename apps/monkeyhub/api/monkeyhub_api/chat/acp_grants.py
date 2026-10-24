"""Which of a Codex agent's permission requests a person can allow once for a whole project (A4).

An ACP permission request names a tool call (its ``kind``, a title, the raw input) and the adapter's own options
(allow once, allow always, reject). Three kinds of request can be allowed for a project: a command the agent starts
(``execute``), going online (``fetch``), and a call to MonkeyHub's own tools (an MCP call to the ``monkeyhub``
server, which the Hub gates and attributes). Everything else is asked every time: an edit above all, since the
bound project is read-only to every agent's file tools (ADR-012), and a tool of any other MCP server, which a
grant for commands must not quietly cover. ``settings.tool_grants`` keeps the answers.

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
ACTION_OF_KIND = {"execute": "run", "fetch": "network"}
# The MCP server the Hub starts for a conversation; its tools are the Hub's own.
HUB_SERVER = "monkeyhub"
_PREVIEW = 160


def action_of(request: Mapping[str, Any]) -> str | None:
    """``run``, ``network`` or ``hub`` when this request is one a person can allow for a project, else None."""

    tool = request.get("toolCall")
    if not isinstance(tool, Mapping):
        return None
    meta = tool.get("_meta") if isinstance(tool.get("_meta"), Mapping) else {}
    if meta.get("is_mcp_tool_call") is True:
        raw = tool.get("rawInput") if isinstance(tool.get("rawInput"), Mapping) else {}
        return "hub" if raw.get("server") == HUB_SERVER else None
    return ACTION_OF_KIND.get(str(tool.get("kind") or ""))


def preview(request: Mapping[str, Any]) -> str:
    """What the request is about in one short line: the command it runs or the address it fetches, if it says."""

    tool = request.get("toolCall")
    raw = tool.get("rawInput") if isinstance(tool, Mapping) else None
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
