"""The Agent's surface for revising a design while the design loop is enforced (#185).

Four tools over the one-call revision, ``POST /api/revisions``:

- ``design_read``: what a change is written from - the working design, what the architect kept and
  rejected in their own words, every condition with whether a check can verify it, the last landed
  change - and the model itself; and, for a message about marks on a page, what each mark lies on;
- ``design_submit``: one change, carried through every check onto the working design or stopped with
  the reason;
- ``design_ask``: the question only the architect can settle, with the reason it cannot be settled here,
  or, for marks, the changes the marks were read as, which wait on the architect's card until they run them;
- ``design_report_limitation``: what MonkeyHub cannot do for this request.

Each call goes through ``studio_request`` and its bindings; nothing here talks to a Runtime of its own.
The Hub holds, for each user message the Agent answers, how many changes came back with problems: after
``repair_budget()`` of them a further submission is refused and the Agent asks or reports instead.

A turn ends in one of three ways: a change landed, the architect was asked, or a limitation was reported.
When the CLI that runs the turn names a loop-state file (``MONKEYHUB_DESIGN_LOOP_STATE``), every outcome
is written there too, and this file's ``stop-hook`` command, run as the CLI's Stop hook, holds the turn
open until it has ended one of those ways (at most ``STOP_BLOCKS`` times). ``cli_profile`` returns the
system prompt, settings, environment and allowed tools of such a turn, for whoever starts the CLI.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

NO_SHELL = ("Bash", "PowerShell")
TOOLS = ("design_read", "design_submit", "design_ask", "design_report_limitation")
STATE_ENV = "MONKEYHUB_DESIGN_LOOP_STATE"
REPAIR_BUDGET_ENV = "MONKEYHUB_REPAIR_BUDGET"
REPAIR_BUDGET = 3
STOP_BLOCKS = 3
CLOSED = ("landed", "admitted")
_KINDS = ["construction", "facets", "hosted-opening", "elevation", "proposal"]

SYSTEM_PROMPT = (
    "You revise one architectural design in MonkeyHub for the architect, who is not watching. "
    "Read it with design_read, then make the change their message asks for with design_submit. "
    "The design states what it keeps, what was rejected and the conditions it must hold; anything it does not "
    "state counts as absent, so ask rather than assume. End the turn in exactly one of three ways: the change "
    "landed (say what changed, from the comparison), you asked the architect with design_ask, or you reported "
    "with design_report_limitation what MonkeyHub cannot do. When design_read lists changes made outside MonkeyHub "
    "(outside), or you are told of them, name each one with its amount in your answer and ask the architect whether "
    "to adopt it; the design keeps its record's version until they say. Never describe a change that "
    "did not land as done."
)

GUIDE = chr(10).join([
    "Revise the design through one call. design_read first: working (runId, stateDigest), keep, rejected,",
    "conditions (checkable or not, and how each stands now), outside, lastChange and the model by geometry id.",
    "design_submit {sourceRunId, stateDigest, kind, proposal, summary, keep?, land?} with the working",
    "design's runId and stateDigest. kind and proposal are a proposal route and its body, for example:",
    '- construction {script, parameters?}: parameters is a list of {key, value} (unit, expr optional). For parameters',
    '  alone the script is "pass": {"script": "pass", "parameters": [{"key": "door_width", "value": 1.4}]}. A parameter',
    "  missing from the model's declaredParameters is derived: it changes through those it comes from, or with a new",
    "  expr (null fixes it at the value). A script edits shapes by the model's geometry ids and parts (get(id)); a part in",
    "  notScriptEditable changes through its madeFrom parameters; an opening through the parameters it lists; a script",
    "  that only prints changes nothing (read with design_read part=model). design_read part=vocabulary is the language.",
    '- hosted-opening {host, kind, along, width, sill, head} adds a door or window: {"host": "wall-south", "kind":',
    '  "window", "along": 2.0, "width": 1.2, "sill": 0.9, "head": 2.1} (metres from the host\'s base; sill 0 for a door).',
    '- facets {targets: [{id, set?, remove?}]}: {"targets": [{"id": "wall-south", "set": {"material.name": "brick"}}]}.',
    '- elevation {"action": "set-datum", "levelId": "level-2", "value": 3.3} moves or adds a level.',
    '- proposal {"semanticEdit": {"summary": "door 1.4 m wide", "parameters": [{"key": "door_width", "value": 1.4}]}}',
    "  edits record rows (entities, parameters, relations) whole.",
    "keep is only for further entity:<id> or parameter:<key> refs the architect's message asks to keep; the design's",
    "keep decisions and conditions apply to every change without it. A refused body comes back with expected: its kind's",
    "fields and an example. It answers one outcome: landed (done), problems (nothing moved; correct the change and",
    "submit again), needs_question (nothing built; call design_ask with that question), refused (read again).",
    "A condition no check can verify is the architect's to confirm, never yours; a rejected option, or a kept target",
    "no checked condition states, is never changed from here. A keep a checked condition states is that condition:",
    "submit the change, and MonkeyHub checks it on the built model and says what it measured. outside lists each change made outside MonkeyHub, newest file first, none in the",
    "design: name each with its amount and ask the architect whether to adopt it; a change that reaches one asks first.",
    "After {budget} changes with problems, ask or report a limitation instead of submitting again.",
    "A message about marks on a page: design_read part=marks says what each mark lies on; ask with design_ask",
    "changes before building anything (the message says how), and build only the items the architect runs.",
])


# Each kind's body, as a refused one is answered: its fields and the smallest example.
SHAPES = {
    "construction": {"fields": 'script (a construction script; "pass" when parameters alone change), parameters?: '
                               "[{key, value, unit?, expr?}]",
                     "example": {"script": "pass", "parameters": [{"key": "door_width", "value": 1.4}]}},
    "hosted-opening": {"fields": "host (a geometry id whose capabilities include hosted-opening), kind (door or window), "
                                 "along, width, sill, head (metres from the host's base; sill 0 for a door)",
                       "example": {"host": "wall-south", "kind": "window", "along": 2.0, "width": 1.2, "sill": 0.9,
                                   "head": 2.1}},
    "facets": {"fields": "targets: [{id, part?, set?: {facet key: value}, remove?: [facet key]}]",
               "example": {"targets": [{"id": "wall-south", "set": {"material.name": "brick"}}]}},
    "elevation": {"fields": 'action "set-datum", levelId, value (the level\'s elevation in metres)',
                  "example": {"action": "set-datum", "levelId": "level-2", "value": 3.3}},
    "proposal": {"fields": "semanticEdit: {summary, entities?, parameters?, relations?, removeEntityIds?, "
                           "removeParameterKeys?, removeRelationIds?}: record rows, whole; at least one edit",
                 "example": {"semanticEdit": {"summary": "door 1.4 m wide",
                                              "parameters": [{"key": "door_width", "value": 1.4}]}}},
}
# The design's own decisions and conditions apply to every change; keep adds only parts.
_NOT_KEPT = ("obligation:", "decision:")


def _as_rows(parameters: Any) -> Any:
    """Parameters given as key: value (or key: {value, ...}) as the list of rows the proposal routes take."""

    if not isinstance(parameters, Mapping):
        return parameters
    return [{"key": key, **value} if isinstance(value, Mapping) else {"key": key, "value": value}
            for key, value in parameters.items()]


def _normalized(body: dict) -> list[str]:
    """Correct, in place, what a body says another way than its route takes; the notes say what was changed."""

    notes = []
    proposal = body.get("proposal")
    if isinstance(proposal, Mapping):
        proposal = dict(proposal)
        if isinstance(proposal.get("parameters"), Mapping):
            proposal["parameters"] = _as_rows(proposal["parameters"])
            notes.append("parameters came as key: value and were sent as a list of {key, value}")
        edit = proposal.get("semanticEdit")
        if isinstance(edit, Mapping) and isinstance(edit.get("parameters"), Mapping):
            proposal["semanticEdit"] = {**edit, "parameters": _as_rows(edit["parameters"])}
            notes.append("semanticEdit.parameters came as key: value and were sent as a list of {key, value}")
        body["proposal"] = proposal
    if isinstance(body.get("keep"), list):
        aside = [ref for ref in body["keep"] if isinstance(ref, str) and ref.startswith(_NOT_KEPT)]
        if aside:
            body["keep"] = [ref for ref in body["keep"] if ref not in aside]
            if not body["keep"]:
                del body["keep"]
            notes.append(f"keep takes entity: and parameter: refs; {', '.join(aside)} apply to every change "
                         "without it and were left out")
    return notes


def repair_budget() -> int:
    """How many changes may come back with problems for one user message: MONKEYHUB_REPAIR_BUDGET, else 3."""
    try:
        value = int(os.environ.get(REPAIR_BUDGET_ENV, "").strip() or REPAIR_BUDGET)
    except ValueError:
        return REPAIR_BUDGET
    return max(0, value)


def tool_definitions() -> list[dict]:
    budget = repair_budget()
    text = {"type": "string", "minLength": 1}
    return [
        {"name": "design_read", "description": GUIDE.replace("{budget}", str(budget)), "inputSchema": {
            "type": "object", "properties": {
                "part": {"type": "string", "enum": ["all", "context", "model", "vocabulary", "marks"],
                         "default": "all", "description": "all is the context and the model; vocabulary is the "
                         "construction language; marks is what each mark of the architect's message lies on."}},
            "additionalProperties": False}},
        {"name": "design_submit", "description": "Carry one change onto the working design through every check "
         "(POST /api/revisions); the chat binds the architect's message and words. See design_read for the fields.",
         "inputSchema": {"type": "object", "properties": {
             "sourceRunId": text, "stateDigest": text, "kind": {"type": "string", "enum": _KINDS},
             "proposal": {"type": "object", "additionalProperties": True}, "summary": {**text, "maxLength": 2000},
             "keep": {"type": "array", "items": {"type": "string"}},
             "land": {"type": "boolean", "default": True},
             "feedbackQuote": {**text, "maxLength": 2000, "description": "The exact passage of the architect's message "
                               "the change answers, when not the whole message."}},
             "required": ["sourceRunId", "stateDigest", "kind", "proposal", "summary"], "additionalProperties": False}},
        {"name": "design_ask", "description": "Ask the architect what only they can settle, and end the turn with it. "
         "reason says why it cannot be settled here (a kept target, a rejected option, a condition no check can "
         "verify, something the design does not state).", "inputSchema": {"type": "object", "properties": {
             "question": {**text, "maxLength": 2000}, "reason": {**text, "maxLength": 1000},
             "refs": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
             "changes": {"type": "array", "maxItems": 30, "description": "For a message about marks: the changes you "
                         "read them as, numbered n = 1, 2, 3; the architect runs them from a card.", "items": {
                             "type": "object", "properties": {
                                 "n": {"type": "integer", "minimum": 1}, "markIds": {"type": "array", "items": text},
                                 "note": {**text, "maxLength": 400, "description": "Whole clauses of the architect's "
                                          "words, exactly as written."},
                                 "understanding": {**text, "maxLength": 240}, "understandingZh": {**text, "maxLength": 240},
                                 "targets": {"type": "array", "items": text}, "protected": {"type": "array", "items": text},
                                 "unsure": {"type": "boolean"}, "extra": {"type": "boolean"}},
                             "required": ["n", "note", "understanding"], "additionalProperties": False}}},
             "required": ["question", "reason"], "additionalProperties": False}},
        {"name": "design_report_limitation", "description": "Report what MonkeyHub cannot do for this request (a missing "
         "operation, not a missing fact), and end the turn with it.", "inputSchema": {"type": "object", "properties": {
             "capability": {**text, "maxLength": 300}, "detail": {**text, "maxLength": 2000}},
             "required": ["capability", "detail"], "additionalProperties": False}},
    ]


def _marks(hub: str, chat_id: str) -> dict:
    """What each mark of the current user message lies on, from its exact saved page revision (#300 3-3)."""
    from urllib.parse import urlencode

    from .. import projects
    from ..models import HubFailure
    from . import judgments, tool_calls, transport

    session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}")
    markup = judgments._user_message(chat_id, session, "Reading marks").get("markup")
    if not markup:
        raise HubFailure(409, "MARKS_MISSING", "This message names no marks on a page.")
    target = {key: markup[key] for key in ("runId", "assetSha256", "pageIndex", "drawingRevisionRef")
              if markup.get(key) is not None}
    page = tool_calls.call_tool(hub, chat_id, "studio_request", {"method": "GET", "path": "/api/document-annotations?"
                                + urlencode({**target, "revisionSha256": markup["revisionSha256"]})})
    ink = page.get("annotations") or []
    read = tool_calls.call_tool(hub, chat_id, "studio_request", {"method": "POST", "path": "/api/markup/resolve",
                                                                 "body": {"page": target, "marks": ink}})
    return {"page": target, "comment": page.get("comment") or "",
            "marks": [{"markId": row.get("id"), "kind": row.get("kind"),
                       **({"label": row["label"]} if row.get("label") else {})} for row in ink],
            "source": read.get("source"), "stateDigest": read.get("stateDigest"), "readings": read.get("readings") or []}


# ---- the loop state of each answered user message ------------------------------------------------

_loops: dict[str, dict[str, Any]] = {}


def _message_id(hub: str, chat_id: str) -> str:
    from .. import projects
    from . import judgments, transport

    session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}")
    return str(judgments._user_message(chat_id, session, "A design change")["id"])


def _loop(chat_id: str, message_id: str) -> dict[str, Any]:
    held = _loops.get(chat_id)
    if held is None or held["messageId"] != message_id:
        held = {"messageId": message_id, "submits": [], "asked": None, "limitation": None, "blocks": 0}
        _loops[chat_id] = held
    return held


def _write_state(held: Mapping[str, Any]) -> None:
    path = os.environ.get(STATE_ENV, "").strip()
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(target.name + ".tmp")
    scratch.write_text(json.dumps(held, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(scratch, target)


REQUEST_FILE_ENV = "MONKEYHUB_DESIGN_REQUEST_FILE"


def publish_request(hub: str, chat_id: str, token: str) -> None:
    """Publish the request a headless design turn answers as the user's message in its bound conversation."""
    from uuid import uuid4

    from . import tool_calls
    from ..models import HubFailure

    path = os.environ.get(REQUEST_FILE_ENV, "").strip()
    if not path:
        raise HubFailure(409, "DESIGN_REQUEST_MISSING", f"A design-loop connection for an external host publishes the "
                         f"request named by {REQUEST_FILE_ENV}; none is set, so no change can answer it.")
    try:
        words = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HubFailure(409, "DESIGN_REQUEST_MISSING", f"The request file could not be read: {exc}") from exc
    if not words:
        raise HubFailure(409, "DESIGN_REQUEST_MISSING", "The request file is empty.")
    message = str(uuid4())
    tool_calls._present_tool(hub, chat_id, {"turnId": message, "messageId": message, "kind": "user", "content": words},
                             token)


def call(hub: str, chat_id: str, name: str, arguments: Mapping[str, Any]) -> dict:
    """Answer one design tool call through studio_request, and keep this message's loop state."""
    from . import tool_calls
    from ..models import HubFailure

    if not isinstance(arguments, Mapping):
        raise HubFailure(422, "CHAT_TOOL_INVALID", f"{name} takes an object of arguments.")
    allowed = {item["name"]: set(item["inputSchema"]["properties"]) for item in tool_definitions()}
    unknown = sorted(set(arguments) - allowed[name])
    if unknown:
        raise HubFailure(422, "CHAT_TOOL_INVALID", f"{name} has no argument {', '.join(unknown)}.")
    if name == "design_read":
        part = arguments.get("part", "all")
        reads = {"context": "/api/revisions/context", "model": "/api/construction/model", "vocabulary": "/api/construction"}
        if part == "marks":
            return {"marks": _marks(hub, chat_id)}
        wanted = ("context", "model") if part == "all" else (part,)
        if any(item not in reads for item in wanted):
            raise HubFailure(422, "CHAT_TOOL_INVALID", "part is all, context, model, vocabulary or marks.")
        return {item: tool_calls.call_tool(hub, chat_id, "studio_request", {"method": "GET", "path": reads[item]})
                for item in wanted}
    held = _loop(chat_id, _message_id(hub, chat_id))
    if name == "design_ask" and arguments.get("changes"):
        from . import transport

        try:
            # The Hub checks the list against its own reading of the marks and the architect's words (#300 3-3).
            card = transport._request_json(hub, "/api/attention/change-lists", "POST", {
                "sessionId": chat_id, "messageId": held["messageId"], "question": arguments["question"],
                "changes": list(arguments["changes"])}, timeout=60,
                headers={"Authorization": "Bearer " + os.environ.get("MONKEYHUB_PRESENTATION_TOKEN", "")})
        except HubFailure as exc:
            if exc.status != 422:
                raise
            return {"recorded": None, "code": exc.error.code, "detail": exc.error.detail, "note": "Nothing was asked: "
                    "correct the list as the detail says and ask again, or ask without changes."}
        held["asked"] = {"question": arguments["question"], "reason": arguments["reason"],
                         "refs": list(arguments.get("refs") or ()), "changeList": card["id"]}
        _write_state(held)
        return {"recorded": "asked", "cardId": card["id"], "note": "Say in your reply how you read the marks, item by "
                "item, and that the list waits on the architect's card, every sure item ticked; end the turn. Nothing "
                "is built until they run it."}
    if name == "design_ask":
        held["asked"] = {"question": arguments["question"], "reason": arguments["reason"],
                         "refs": list(arguments.get("refs") or ())}
        _write_state(held)
        from . import transport

        try:
            # The question also waits in the architect's queue (#300), answered back into this conversation.
            transport._request_json(hub, "/api/attention/questions", "POST", {
                "sessionId": chat_id, "messageId": held["messageId"], **held["asked"]}, timeout=10,
                headers={"Authorization": "Bearer " + os.environ.get("MONKEYHUB_PRESENTATION_TOKEN", "")})
        except (HubFailure, OSError, TimeoutError, ValueError):
            pass  # The reply still carries the question; only its card is missing.
        return {"recorded": "asked", "note": "Put this question to the architect in your reply, in their words, and end "
                "the turn; nothing more is changed until they answer."}
    if name == "design_report_limitation":
        held["limitation"] = {"capability": arguments["capability"], "detail": arguments["detail"]}
        _write_state(held)
        return {"recorded": "limitation", "note": "Tell the architect what MonkeyHub could not do and what would have "
                "been needed, and end the turn."}
    failed = [row for row in held["submits"] if row["outcome"] == "problems"]
    if len(failed) >= repair_budget():
        raise HubFailure(409, "REPAIR_BUDGET_EXHAUSTED", f"{len(failed)} changes for this message came back with problems; "
                         "ask the architect with design_ask, or report what MonkeyHub cannot do with "
                         "design_report_limitation. Nothing was submitted.")
    body = {key: arguments[key] for key in ("sourceRunId", "stateDigest", "kind", "proposal", "summary", "keep", "land")
            if key in arguments}
    notes = _normalized(body)
    # Earlier attempts of this message on the same base are the ones this result replaces.
    attempts = [row["candidateId"] for row in failed if row.get("candidateId") and row["base"] == arguments["stateDigest"]]
    if attempts:
        body["supersedes"] = attempts
    # Readback checks a design-loop change; these tools take no look, so the loop is a deterministic edit.
    request = {"method": "POST", "path": "/api/revisions", "body": body, "taskClass": "deterministic_edit"}
    if "feedbackQuote" in arguments:
        request["feedbackQuote"] = arguments["feedbackQuote"]
    answer = tool_calls.call_tool(hub, chat_id, "studio_request", request)
    held["submits"].append({"outcome": answer.get("outcome"), "code": answer.get("code"),
                            "candidateId": answer.get("candidateId"), "base": arguments["stateDigest"]})
    _write_state(held)
    left = repair_budget() - sum(1 for row in held["submits"] if row["outcome"] == "problems")
    if answer.get("outcome") == "problems":
        answer = {**answer, "repairsLeft": max(0, left)}
    if answer.get("code") == "PROPOSAL_REFUSED" and arguments.get("kind") in SHAPES:
        answer = {**answer, "expected": {"kind": arguments["kind"], **SHAPES[arguments["kind"]]}}
    if notes:
        answer = {**answer, "notes": notes}
    return answer


# ---- the turn's Stop hook --------------------------------------------------------------------------

def stop_decision(state: Mapping[str, Any] | None, blocks: int = 0) -> tuple[bool, str | None]:
    """(may the turn end, why not): it ends once a change landed, the architect was asked or a limit reported.

    ``blocks`` is how many times this turn was already held open; after ``STOP_BLOCKS`` it may end unclosed.
    """

    if not state or not state.get("submits") or state.get("asked") or state.get("limitation"):
        return True, None
    last = state["submits"][-1]
    if last.get("outcome") in CLOSED or blocks >= STOP_BLOCKS:
        return True, None
    if last.get("outcome") == "needs_question":
        return False, ("The last change stopped with a question only the architect can settle "
                       f"({last.get('code')}). Call design_ask with that question before ending the turn.")
    return False, (f"The last change did not land ({last.get('outcome')}: {last.get('code')}). Correct it and call "
                   "design_submit again, ask the architect with design_ask, or report what MonkeyHub cannot do with "
                   "design_report_limitation, before ending the turn.")


def _stop_hook(state_file: Path) -> int:
    """Hold the turn open, or let it end; the hook's own count of holds sits beside the loop state."""

    try:
        sys.stdin.read()
    except (OSError, ValueError):
        pass
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    counter = state_file.with_name(state_file.name + ".stop.json")
    try:
        held = json.loads(counter.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        held = {}
    message = None if not state else state.get("messageId")
    blocks = held.get("blocks", 0) if held.get("messageId") == message else 0
    allowed, reason = stop_decision(state, blocks)
    if not allowed or (blocks and state):
        unclosed = allowed and blocks >= STOP_BLOCKS
        counter.write_text(json.dumps({"messageId": message, "blocks": blocks + (0 if allowed else 1),
                                       "unclosed": unclosed}) + "\n", encoding="utf-8", newline="\n")
    if not allowed:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def cli_profile(state_file: Path, *, request_file: Path | None = None, hub_url: str | None = None,
                project_dir: Path | None = None, session_id: str | None = None, python: str = sys.executable,
                server: str = "monkeyhub") -> dict[str, Any]:
    """What a CLI turn in the design loop is started with: prompt, settings with the Stop hook, environment, tools.

    ``request_file`` holds the words an external host's turn answers; its connection publishes them as the
    user's message. With ``hub_url``, ``project_dir`` and ``session_id`` the answer also holds ``mcp_config``,
    ready for ``--mcp-config``: this Hub's tool server bound to that project and session, design-loop tools only.
    """

    hook = f'"{Path(python).as_posix()}" "{Path(__file__).resolve().as_posix()}" stop-hook --state "{Path(state_file).as_posix()}"'
    env = {"MONKEYHUB_DESIGN_LOOP": "enforced", STATE_ENV: str(state_file), REPAIR_BUDGET_ENV: str(repair_budget()),
           "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": str(STOP_BLOCKS + 1), "MCP_TOOL_TIMEOUT": "480000",
           **({} if request_file is None else {REQUEST_FILE_ENV: str(request_file)})}
    profile: dict[str, Any] = {
        "system_prompt": SYSTEM_PROMPT,
        "settings": {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": hook, "timeout": 30}]}]}},
        "env": env,
        "mcp_args": ["--design-loop"],
        "allowed_tools": [f"mcp__{server}__{name}" for name in TOOLS],
        # No shell, whatever else the host allows: a shell could reach the Runtime's port and act as a person.
        "disallowed_tools": list(NO_SHELL),
        "permission_mode": "dontAsk",
    }
    if hub_url is not None and project_dir is not None and session_id is not None:
        args = [str(Path(__file__).resolve().with_name("mcp_server.py")), "--mcp", "--hub-url", hub_url,
                "--project-dir", str(project_dir), "--source-session-id", session_id, "--provider", "claude",
                "--design-loop"]
        profile["mcp_config"] = {"mcpServers": {server: {"command": python, "args": args, "env": dict(env),
                                                          "alwaysLoad": True}}}
    return profile


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The design loop's Stop hook.")
    commands = parser.add_subparsers(dest="command", required=True)
    stop = commands.add_parser("stop-hook")
    stop.add_argument("--state", required=True)
    args = parser.parse_args()
    raise SystemExit(_stop_hook(Path(args.state)))
