"""One tool call of a turn as one readable transcript row.

The row names the call and the values a person can act on; only this adapter's
own project tools, and only when they succeeded, name the candidate a finished
run made. The rest of a result stays with the CLI that has it.
"""

from __future__ import annotations

import json
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from . import providers, studio_tool

from ..models import ChatMessage


# What a tool result is asked for by name. Anything else stays in the result
# the CLI already has; a transcript is not a place to copy a state or a schema.
_ACTIVITY_NAMES = ("candidateId", "jobId", "proposalId", "runId", "status", "readback",
                   "waitedOut", "code", "detail", "message")
_ACTIVITY_PREVIEW = 320
# How the Claude CLI names this adapter's tools in its stream, and the tools
# this adapter exposes. A CLI's own file tools are activity too, but only these
# speak for the project, so only their results may name a candidate.
_CLAUDE_TOOL_PREFIX = "mcp__monkeyhub__"
_BOUND_TOOLS = ("studio_schema", "studio_request", "fab_request")


def _tool_text(value) -> str:
    """The MCP text content of one result or error, without its envelope."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        rows = value.get("content")
        if isinstance(rows, list):
            parts = [row["text"] for row in rows if isinstance(row, dict) and isinstance(row.get("text"), str)]
            if any(parts):
                return "\n".join(part for part in parts if part)
    if value in (None, {}, []):
        return ""
    return json.dumps(value, ensure_ascii=False)


def _tool_values(body: str, requested_run: str | None = None) -> tuple[list[str], str | None]:
    """The named short values of a JSON result, and a finished candidate.

    A call that asked for one exact run and was answered about that same run
    names it too, so an earlier candidate stays openable from its own row
    rather than through a latest-candidate fallback.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return [], None
    if not isinstance(parsed, dict):
        return [], None
    if isinstance(parsed.get("path"), str) and isinstance(parsed.get("method"), str):
        # A schema read: name the action it described, never copy the schema.
        action = f"{parsed['method']} {parsed['path']}"
        if str(parsed.get("note", "")).startswith(studio_tool._CONTRACT_NOTE) or isinstance(parsed.get("operation"), dict):
            return [f"read the schema of {action}"], None
    if isinstance(parsed.get("actions"), list) and isinstance(parsed.get("total"), int):
        return [f"listed {parsed['total']} actions" + (" with their guide" if "guide" in parsed else "")], None
    named = [f"{name}: {parsed[name]}" for name in _ACTIVITY_NAMES
             if isinstance(parsed.get(name), (str, int, float, bool)) and len(str(parsed[name])) <= 200]
    # A run whose result could not be read is not a candidate to open: the row
    # says the job finished and that the reading of it did not, and the card
    # stays for the ones that were really read back.
    read_back = parsed.get("readback")
    complete = parsed.get("status") == "succeeded" and (read_back is None or read_back == "ok")
    candidate = parsed.get("candidateId") if complete else None
    if not (isinstance(candidate, str) and candidate):
        candidate = None
        reference = parsed.get("referenceRun")
        if requested_run and isinstance(reference, dict) and reference.get("runId") == requested_run:
            candidate = requested_run
    return named, candidate


def _claude_call(block: Mapping, asked: ChatMessage | None) -> dict:
    """One Claude `tool_use` or `tool_result` block as the same MCP call item.

    Claude sends the two halves as separate events joined only by
    `tool_use_id`, and the result half names neither the tool nor what was
    asked. The call's own row already wrote that down, so the result reuses it
    rather than inventing a second record of the same call.
    """
    if block.get("type") == "tool_use":
        name = str(block.get("name") or "tool")
        bound = name.startswith(_CLAUDE_TOOL_PREFIX)
        return {
            "type": "mcp_tool_call", "id": block.get("id"),
            # Only this adapter's own tools speak for the project; the CLI's
            # built-in file tools are shown as what they are.
            "server": "monkeyhub" if bound else None,
            "tool": name[len(_CLAUDE_TOOL_PREFIX):] if bound else name,
            "arguments": block.get("input"), "status": "in_progress", "result": None, "error": None,
        }
    content = block.get("content")
    text = _tool_text({"content": content} if isinstance(content, list) else content)
    head = (asked.content.splitlines()[0].split(" · ") if asked and asked.content else [])
    method, _, path = (head[1] if len(head) > 1 else "").partition(" ")
    failed = bool(block.get("is_error"))
    return {
        "type": "mcp_tool_call", "id": block.get("tool_use_id"),
        "server": "monkeyhub" if head[:1] and head[0] in _BOUND_TOOLS else None,
        "tool": head[0] if head else "tool",
        "arguments": {"method": method, "path": path} if path else {},
        "status": "failed" if failed else "completed",
        "result": None if failed else text,
        "error": text if failed else None,
    }


def _computer_line(body: str) -> str | None:
    """One ComputerActionReceipt@1 as the line a person reads in the transcript.

    What happened on the screen is a verb and the thing it reached, so that is
    the line: CLICK — Save, with the verdict the action declared beside it. A
    refusal says its code instead, because there is nothing to tick.
    """
    try:
        receipt = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(receipt, dict) or receipt.get("schema") != "ComputerActionReceipt@1":
        return None

    def part(key: str) -> dict:
        value = receipt.get(key)
        return value if isinstance(value, dict) else {}

    intent = str(receipt.get("intent") or receipt.get("application") or "")
    code = part("refusal").get("code")
    code = code if isinstance(code, str) else None
    if receipt.get("status") == "refused" and code:
        return f"REFUSED {code} — {intent}".strip()
    resolved = part("target").get("resolved")
    named = ((resolved or {}).get("name") or part("window").get("title")
             or receipt.get("application") or intent)
    verdict = part("verification").get("status")
    said = " ✓" if verdict == "passed" else " ✕" if verdict == "failed" else ""
    if not said and receipt.get("status") == "failed" and code:
        # A step that failed without a declared expectation still says so.
        said = f" ✕ {code}"
    return f"{str(part('action').get('type') or 'action').upper()} — {named}{said}"


def _tool_activity(item: Mapping, environment=None) -> tuple[str, str | None, bool]:
    """One MCP call as a readable line plus collapsible diagnostics.

    The summary is the first line. The rest names the values a person can act
    on; a result that names none is previewed and its remaining size stated.
    """
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    if not isinstance(arguments, dict):
        arguments = {}
    result = item.get("result")
    refusal = _tool_text(item.get("error"))
    status = str(item.get("status") or "")
    failed = bool(refusal) or status == "failed" or (isinstance(result, dict) and result.get("isError") is True)
    # The summary is one line whatever the CLI put in the arguments.
    head = " ".join(" · ".join(part for part in (
        str(item.get("tool") or item.get("server") or "tool"),
        " ".join(str(arguments[name]) for name in ("method", "path") if isinstance(arguments.get(name), str)),
        "failed" if failed else status,
    ) if part).split())[:300]
    body = refusal if failed and refusal else _tool_text(result)
    asked = arguments.get("path") if isinstance(arguments.get("path"), str) else ""
    # A refused or failed call names no candidate, whatever it asked for.
    requested_run = None if failed else next(iter(parse_qs(urlsplit(asked).query).get("run", [])), None)
    named, candidate = _tool_values(body, requested_run)
    if failed or item.get("server") != "monkeyhub":
        # A failed call, or one made with the CLI's own tools, names no
        # candidate: reading a record file is not a finished run.
        candidate = None
    if not failed and item.get("tool") == "computer_action":
        # A desktop step is one sentence: the verb, what it reached and whether
        # what it promised held. The receipt itself stays in the trace.
        head = _computer_line(body) or head
    if named:
        lines = named + ([f"… full result {len(body)} characters"] if len(body) > _ACTIVITY_PREVIEW else [])
    elif body:
        preview = body[:_ACTIVITY_PREVIEW]
        lines = [preview] + ([f"… {len(body) - len(preview)} more characters"] if len(body) > len(preview) else [])
    else:
        lines = []
    return providers._redact("\n".join([head, *lines]), environment), candidate, failed
