"""Project-bound conversations backed by the installed coding CLIs.

The CLI retains its native conversation. Hub retains the visible transcript and
that exact session id under its configured runtime root. Design writes stay on
the existing Studio interfaces, reached through the small stdio tool below.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import Future, InvalidStateError
from contextlib import contextmanager, suppress
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import importlib.util
import mimetypes
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import secrets
from io import BytesIO
import signal
import subprocess
import sys
import threading
import time
from typing import Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

# A CLI starts this same file as its stdio MCP connection, including from a
# packaged interpreter which does not inherit the launcher's sys.path.
if __package__ in {None, ""}:
    _source = Path(__file__).resolve().parents[4]
    for _path in (_source, _source / "apps/archflow-studio/api", Path(__file__).resolve().parents[1]):
        sys.path.insert(0, str(_path))
    __package__ = "monkeyhub_api"
    # Started as a script this module is __main__, so a sibling importing it by
    # name would execute a second copy with its own context variables and its
    # own trace headers. One file is one module, whichever way it was started.
    sys.modules.setdefault("monkeyhub_api.chat", sys.modules[__name__])

from archflow.project.refs import ProjectRecordRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord
from archflow_studio_api.settings import read_application_settings

from pydantic import Field

from .models import (
    ChatAttachment, ChatAttention, ChatCreateRequest, ChatDesignContext, ChatDetail, ChatMessage, ChatPostRequest, ChatProject,
    ChatDocument, ChatDocumentRef, ChatPresentationBindRequest, ChatPresentationBinding, ChatPresentationRequest,
    ChatPermission, ChatPermissionOption, ChatPermissionRequest,
    ChatProjectRequest, ChatProvider, ChatSummary, ChatUsageSource, ChatWorkspace, HubError, HubFailure,
)
from .chat_trace import HubTurnObserver
from monkeymonitor.store import UsageLog

_trace_headers = ContextVar("hub_tool_trace_headers", default={})
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _verify_image(data: bytes, mime_type: str) -> None:
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as image:
            if Image.MIME.get(image.format) != mime_type:
                raise ValueError("The image format does not match its MIME type.")
            image.verify()
    except Exception as exc:
        raise HubFailure(422, "CHAT_IMAGE_INVALID", "This attachment is not a valid image of the declared type.") from exc


def _attachment_read_paging(offset: int, limit: int, page: int) -> None:
    if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536
            or type(page) is not int or page < 1):
        raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "Use offset >= 0, limit from 1 to 65536, and page >= 1.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.") from exc
    if parsed != value:
        raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
    return parsed


def _project(path: str) -> tuple[str, str]:
    root = Path(path)
    if not root.is_absolute():
        raise HubFailure(422, "CHAT_PROJECT_INVALID", "Choose an existing absolute project folder.")
    try:
        repository = FilesystemProjectRepository.open(root)
        repository.read_head()
    except (OSError, ValueError, RuntimeError) as exc:
        raise HubFailure(422, "CHAT_PROJECT_INVALID", "The selected folder is not a readable ArchFlow project.") from exc
    return repository.layout.project_id, str(repository.layout.root)


def _position(root: str) -> tuple[int | None, str | None]:
    """The published version and accepted Stage this project itself holds.

    Read through the project's existing P036 interfaces. A failure to read is
    reported as "not known" rather than as a version, and a candidate run is
    never one of these answers.
    """
    try:
        repository = FilesystemProjectRepository.open(Path(root))
        version = repository.read_head().version
        branches = repository.read_design_branches()
    except (OSError, ValueError, RuntimeError):
        return None, None
    branch = branches.get("main") or next(iter(branches.values()), None)
    if branch is None:
        return version, None
    try:
        stage = repository.load_json(ProjectRecordRef.from_dict(branch["head_stage"]))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return version, None
    label = stage.get("label")
    return version, label if isinstance(label, str) and label else None


def _workspace(runtime_root: Path, settings) -> Path:
    """Where new projects are created for this Hub run.

    The explicitly saved location, else the folder the currently chosen project
    already lives in, else a `workspace/projects` folder under this Hub's own
    explicitly supplied runtime root. It is a location only; every project write
    still goes through P036.
    """
    if settings.workspace_dir:
        return Path(settings.workspace_dir)
    if settings.project_dir:
        parent = Path(settings.project_dir).parent
        if parent != Path(settings.project_dir):
            return parent
    return runtime_root / "workspace" / "projects"


def _new_project(workspace: Path, name: str) -> Path:
    """Create one empty P036 project in that workspace, as the tool entry does.

    The same initialization `tools/create_project.py` performs: a project at
    version 0 with an empty authored record, no run and no design content. A
    name that is not a project id, an escape out of the workspace, or a folder
    that already holds anything is refused with its own reason.
    """
    chosen = name.strip()
    if not chosen:
        raise HubFailure(422, "PROJECT_NAME_REQUIRED", "Enter a name for the new project.")
    try:
        require_identifier(chosen, "project_id")
    except (ValueError, TypeError) as exc:
        raise HubFailure(422, "PROJECT_NAME_INVALID",
                         "Use letters, digits, hyphens or underscores for the project name.") from exc
    if not workspace.is_absolute():
        raise HubFailure(422, "WORKSPACE_INVALID", "Choose an absolute workspace folder first.")
    root = (workspace / chosen).resolve()
    if root.parent != workspace.resolve():
        raise HubFailure(422, "PROJECT_NAME_INVALID", "The project name cannot name another folder.")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise HubFailure(409, "PROJECT_EXISTS",
                         "A folder of that name already exists here and was left untouched.")
    try:
        # P036 creates the project root itself; Hub chooses no path of its own
        # and writes nothing beside it.
        record = StateRecord(project_id=chosen, run_id="authored", entities=())
        repository = FilesystemProjectRepository.initialize(
            root, project_id=chosen,
            initial_state={"project_id": chosen, "version": 0},
            authored_record=record.to_dict(),
        )
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise HubFailure(422, "PROJECT_CREATE_FAILED",
                         f"The project could not be created here: {exc}") from exc
    return repository.layout.root


# What a headless turn may do without a prompt nobody is there to answer. The
# built-in names are the CLI's own; the prefixed ones are this adapter's
# project tools and session-scoped attachment reader.
_CLAUDE_APPROVED = (
    "Read", "Glob", "Grep", "Write", "Edit", "Bash", "TodoWrite",
    "mcp__monkeyhub__studio_schema",
    "mcp__monkeyhub__studio_request",
    "mcp__monkeyhub__visual_review",
    "mcp__monkeyhub__fab_request",
    "mcp__monkeyhub__attachment_read",
    "mcp__monkeyhub__chat_present",
)


def _claude_approved(runtime_root: Path) -> tuple[str, ...]:
    """The names above, plus computer use on a machine whose policy allows it.

    Driving the desktop is not approved by being installed. The tools are
    always advertised, because their route answers a disabled machine with the
    file that turns them on, but a headless turn may only actually use them
    where the owner of this machine said so.
    """
    # Imported inside every caller rather than at the top: computer_tools
    # reaches its routes through this module's own transport, and one of the
    # two has to be late for the other to exist.
    from . import computer_tools

    if not computer_tools.read_policy(runtime_root).enabled:
        return _CLAUDE_APPROVED
    return _CLAUDE_APPROVED + tuple(
        f"mcp__monkeyhub__{name}" for name in computer_tools.TOOL_NAMES
    )


def _source_checkout() -> Path | None:
    """This Hub's own source tree, when it is one somebody can work in.

    A development checkout answers with its root: the assistant reads and
    changes the code it is running, runs its commands there, and fixes what it
    breaks. An installed bundle is not one — there is no repository and nothing
    to edit — and this answers None so the caller can say so rather than
    pretend the code is writable.
    """
    root = Path(__file__).resolve().parents[4]
    if not (root / ".git").exists() or not (root / "AGENTS.md").is_file():
        return None
    return root if os.access(root, os.W_OK) else None


def _claude_env() -> dict[str, str]:
    values = dict(os.environ)
    root = Path(values.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        saved = json.loads((root / "settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        saved = {}
    for key, value in saved.get("env", {}).items():
        if isinstance(key, str) and isinstance(value, str) and key not in values:
            values[key] = value
    return values


def _redact(text: str, environment: Mapping[str, str] | None = None) -> str:
    """Never expose provider credentials in the transcript or an error body."""
    for key, value in (environment or os.environ).items():
        if re.search(r"key|token|secret|password|access.?code", key, re.I) and len(value) >= 8:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted]", text)
    return re.sub(
        r"""(?i)((?:authorization|api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token)\s*["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,}]+)""",
        r"\1[redacted]", text,
    )


def _cli_commands() -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for kind, candidates in {
        "codex": (shutil.which("codex"), str(Path.home() / "AppData/Roaming/npm/codex.cmd")),
        "claude": (shutil.which("claude"), str(Path.home() / ".local/bin/claude.exe")),
    }.items():
        executable = next((item for item in candidates if item and Path(item).is_file()), None)
        if executable:
            found[kind] = (executable,)
    return found


def _toml_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(key)} = {_toml_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)


def _codex_acp_command() -> tuple[str, ...] | None:
    """Use the application's locked adapter; never download during a turn."""
    hub = Path(__file__).resolve().parents[2]
    bundled_node = hub.parents[1] / "_runtime/node/node.exe"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
    adapter = hub / "node_modules/@agentclientprotocol/codex-acp/dist/index.js"
    if node and adapter.is_file() and importlib.util.find_spec("acp") is not None:
        script = str(adapter)
        # Tauri's canonical Windows source root carries a verbatim prefix.
        # Node's JS entrypoint resolver needs the equivalent drive/UNC path.
        if os.name == "nt":
            if script.lower().startswith("\\\\?\\unc\\"):
                script = "\\\\" + script[8:]
            elif re.match(r"^\\\\\?\\[a-zA-Z]:\\", script):
                script = script[4:]
        return (node, script)
    return None


def _native_codex(command: tuple[str, ...]) -> str:
    """Resolve the native executable in the installed CLI, not the adapter's copy."""
    executable = Path(command[0]).resolve()
    if os.name != "nt" or executable.suffix.lower() == ".exe":
        return str(executable)
    arm = platform.machine().lower() in {"arm64", "aarch64"}
    package = "codex-win32-arm64" if arm else "codex-win32-x64"
    target = "aarch64-pc-windows-msvc" if arm else "x86_64-pc-windows-msvc"
    modules = executable.parent / "node_modules/@openai"
    roots = (modules / "codex/node_modules/@openai" / package, modules / package, modules / "codex")
    for root in roots:
        binary = root / "vendor" / target / "bin/codex.exe"
        if binary.is_file():
            return str(binary.resolve())
    raise HubFailure(503, "CHAT_CODEX_EXECUTABLE_MISSING", "The installed Codex native executable could not be located. Repair the existing Codex installation.")


class _SavedChat(ChatDetail):
    nativeSessionId: str | None = None
    cliStartId: str | None = None
    priorProviderSessionIds: list[str] = []
    # Provider continuity only; the Stage and all design facts remain in P036.
    providerStageRef: str | None = None
    # Records written before ACP retain the exact native CLI continuation path.
    transport: Literal["cli", "acp"] = "cli"
    acpSessionId: str | None = None
    acpDefaultModel: str | None = None
    # Read from the transcript on every summary (#300); a record never stores it.
    attention: ChatAttention | None = Field(default=None, exclude=True)


# A summary is these fields of the record, plus its attention read fresh.
_SUMMARY_FIELDS = set(ChatSummary.model_fields) - {"attention"}


def _attention(session: _SavedChat) -> ChatAttention | None:
    """What this conversation waits for from the architect (#300).

    A permission request waits exactly while its message still carries it:
    the request the conversation shows with its options. Answering, stopping,
    a withdrawn step and a Hub restart all clear it. The record is already in
    memory, so asking reads no file.
    """
    return "permission" if any(message.permission is not None for message in session.messages) else None


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


def _turn_id(session: _SavedChat) -> str:
    """The last user message the Agent was given; see ChatStore._answering for a running turn."""
    return next((row.id for row in reversed(session.messages) if row.role == "user"
                 and row.interjection in {None, "delivered", "restarted"}), "turn")


def _asked(session: _SavedChat) -> ChatMessage | None:
    """The message that started this turn, never an interjection sent during it."""
    return next((row for row in reversed(session.messages) if row.role == "user" and row.interjection is None), None)


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
    if isinstance(parsed.get("operation"), dict) and isinstance(parsed.get("path"), str):
        # A schema read: name the action it described, never copy the schema.
        return [f"read the schema of {parsed.get('method', '')} {parsed['path']}".strip()], None
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
    return _redact("\n".join([head, *lines]), environment), candidate, failed


@dataclass
class _Running:
    stop: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    last_save: float = 0.0
    trace: HubTurnObserver | None = None
    # This turn's own selected source and focus, if the message named one. It
    # lives and dies with the turn: nothing reads it afterwards, and the next
    # message brings its own or none.
    design_context: ChatDesignContext | None = None
    context_mode: Literal["continue", "project", "stage"] = "continue"
    attachments: tuple[tuple[ChatAttachment, Path], ...] = ()
    # #301. The user messages this turn has handed to the Agent, first to last:
    # a call opened under one of them keeps its row when it finishes later.
    turns: list[str] = field(default_factory=list)
    # Interjections not yet with the Agent, in the order they were sent. What
    # is still here when the turn ends becomes the next prompt.
    waiting: list[tuple[str, str]] = field(default_factory=list)
    # Claude reads further user messages from stdin until its turn's result;
    # None when this turn has no such channel or it has closed.
    stdin: queue.Queue | None = None
    # Interjections already written to that stdin and not yet read back.
    written: list[str] = field(default_factory=list)
    # This turn was cancelled to continue with an interjection, not stopped.
    redirected: bool = False
    # The ACP prompt is with the adapter; steering is offered only then.
    prompting: bool = False
    steering: threading.Lock = field(default_factory=threading.Lock)


# How long a connection check stays good before it is asked again, and how long
# any single read-only question to a CLI may take. Nothing here starts a
# conversation or spends a model call: it reads the CLI's own status and its
# own catalogue.
_CHECK_TTL_S = 600.0
_CHECK_TIMEOUT_S = 25.0


def _cli_output(command: list[str], timeout: float = 15.0, environment=None) -> tuple[int, str]:
    """Run one read-only CLI question. Its output is never stored or shown raw."""
    try:
        finished = subprocess.run(
            command, env=environment or os.environ.copy(), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1, ""
    return finished.returncode, finished.stdout


def _codex_models(command: tuple[str, ...]) -> tuple[list[str], str]:
    """The catalogue the installed Codex CLI itself answers with.

    `codex app-server` speaks the CLI's own JSON-RPC protocol; `model/list` is
    its read-only picker listing for the account that CLI is already logged
    into. No prompt is sent and no model is run.
    """
    try:
        process = subprocess.Popen(
            [*command, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError):
        return [], "The installed Codex CLI could not be asked for its model list."
    answers: list[dict] = []

    def read() -> None:
        try:
            for line in process.stdout:
                try:
                    answers.append(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + _CHECK_TIMEOUT_S
    try:
        for request in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "monkeyhub", "title": "MonkeyHub", "version": "0.1.0"}}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}},
        ):
            process.stdin.write(json.dumps(request) + chr(10))
            process.stdin.flush()
        while time.monotonic() < deadline and not any(row.get("id") == 2 for row in answers):
            time.sleep(0.1)
    except (OSError, ValueError):
        pass
    finally:
        # Closing the pipe is how this server is asked to finish; only a server
        # that ignores that is stopped outright.
        try:
            process.stdin.close()
            process.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _stop_process(process)
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=1)
    answer = next((row for row in answers if row.get("id") == 2), None)
    if answer is None or "error" in answer:
        return [], "The installed Codex CLI did not answer with its model list; sign in to it and try again."
    rows = answer.get("result", {}).get("data", [])
    models = [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
              and not row.get("hidden")]
    if not models:
        return [], "The installed Codex CLI listed no models for this account."
    return models, "Listed by the installed Codex CLI for the account it is signed in to."


def _claude_models(command: tuple[str, ...], environment: Mapping[str, str]) -> tuple[list[str], str]:
    """The catalogue the installed Claude CLI itself answers with.

    Its SDK control protocol carries the model list in the `initialize`
    response — the same list `supportedModels()` reads. Only that control
    request is sent: no prompt, no turn, no model call.
    """
    try:
        process = subprocess.Popen(
            [*command, "--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=dict(environment), text=True, encoding="utf-8", errors="replace", bufsize=1,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError):
        return [], "The installed Claude CLI could not be asked for its model list."
    answers: list[dict] = []

    def read() -> None:
        try:
            for line in process.stdout:
                try:
                    answers.append(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + _CHECK_TIMEOUT_S
    try:
        process.stdin.write(json.dumps(
            {"type": "control_request", "request_id": "monkeyhub-models",
             "request": {"subtype": "initialize"}}) + chr(10))
        process.stdin.flush()
        while time.monotonic() < deadline and not any(row.get("type") == "control_response" for row in answers):
            time.sleep(0.1)
    except (OSError, ValueError):
        pass
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _stop_process(process)
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=1)
    answer = next((row for row in answers if row.get("type") == "control_response"), None)
    payload = (answer or {}).get("response") or {}
    rows = payload.get("response", {}).get("models") if isinstance(payload.get("response"), dict) else None
    if payload.get("subtype") != "success" or not isinstance(rows, list):
        return [], "The installed Claude CLI did not answer with its model list; sign in to it and try again."
    models = [row["value"] for row in rows if isinstance(row, dict) and isinstance(row.get("value"), str)]
    if not models:
        return [], "The installed Claude CLI listed no models for this account."
    return models, "Listed by the installed Claude CLI for the account it is signed in to."


def _check_providers(commands: Mapping[str, tuple[str, ...]], environment: Mapping[str, str]) -> dict[str, dict]:
    """Ask each installed CLI, read-only, who it is signed in as and what it lists."""
    found: dict[str, dict] = {}
    if "codex" in commands:
        code, output = _cli_output([*commands["codex"], "login", "status"])
        signed = None if code < 0 else code == 0 and "not logged in" not in output.lower()
        models, detail = _codex_models(commands["codex"]) if signed else (
            [], "Sign in to the installed Codex CLI to read its model list.")
        found["codex"] = {"signedIn": signed, "models": models, "modelDetail": detail}
    if "claude" in commands:
        code, output = _cli_output([*commands["claude"], "auth", "status"], timeout=20.0, environment=dict(environment))
        signed = None
        try:
            signed = bool(json.loads(output).get("loggedIn"))
        except ValueError:
            signed = None if code < 0 else code == 0
        models, detail = _claude_models(commands["claude"], environment) if signed else (
            [], "Sign in to the installed Claude CLI to read its model list.")
        found["claude"] = {"signedIn": signed, "models": models, "modelDetail": detail}
        # Coding Plan runs the same CLI against a configured endpoint of its
        # own, so that endpoint decides what it serves; this list is the CLI's.
        found["coding-plan"] = {"signedIn": None, "models": [],
                                "modelDetail": "Configure this connection's endpoint to use it; the model it serves is that endpoint's, so enter the model id it expects."}
    return found


class ChatStore:
    def __init__(self, runtime_root: Path, hub_url: str, *, applications=None, commands=None, acp_command=None, timeout_s: float = 900):
        self.root = runtime_root / "chats"
        self.runtime_root = runtime_root
        self.usage_log = UsageLog(runtime_root / "diagnostics" / "monkeymonitor")
        self.hub_url = hub_url
        self.applications = applications
        self.commands = commands
        self._use_acp = acp_command is not None or commands is None
        self._acp_command = tuple(acp_command) if acp_command is not None else (_codex_acp_command() if commands is None else None)
        self._acp_sessions = {}
        self._acp_tools: dict[str, dict[str, dict]] = {}
        self._permissions: dict[tuple[str, str], Future] = {}
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self._sessions: dict[str, _SavedChat] = {}
        self._running: dict[str, _Running] = {}
        self._progress_rows: dict[str, dict[str, ChatMessage]] = {}
        self._presentation_tokens: dict[str, str] = {}
        self._loaded = False
        self._closing = False
        self.on_change = None
        # One in-memory answer per Hub run: what the installed CLIs said when
        # they were last asked. Nothing is written to disk and no check runs on
        # the transcript's polling path.
        self._checked: dict[str, dict] = {}
        self._checked_at: float | None = None
        self._checking = False
        self._check_thread: threading.Thread | None = None

    def _load(self) -> None:
        if self._loaded:
            return
        for path in sorted(self.root.glob("*.json")):
            try:
                session = _SavedChat.model_validate_json(path.read_text(encoding="utf-8"))
                _identifier(session.id)
                if path.stem != session.id:
                    raise ValueError("chat filename does not match its id")
            except (ValueError, OSError, HubFailure) as exc:
                raise HubFailure(503, "CHAT_RECORD_INVALID", "A saved chat record could not be read.") from exc
            self._sessions[session.id] = session
            if session.status == "running":
                session.status = "interrupted"
                session.error = HubError(code="CHAT_INTERRUPTED", detail="Hub closed before this turn finished. You can continue this conversation.")
                for message in session.messages:
                    if message.status == "streaming" and not session.sourceSessionId:
                        message.status = "interrupted"
                    if message.interjection == "pending":
                        message.interjection = "undelivered"
                    message.permission = None
                self._save(session)
        self._loaded = True

    def _attachment_path(self, session_id: str, attachment: ChatAttachment) -> Path:
        suffix = Path(attachment.name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        return self.root / _identifier(session_id) / "attachments" / (_identifier(attachment.id) + suffix)

    def attachment(self, session_id: str, attachment_id: str) -> tuple[ChatAttachment, Path]:
        with self._lock:
            session = self._session(session_id)
            attachment = next((item for message in session.messages for item in message.attachments
                               if item.id == attachment_id), None)
            if attachment is None:
                raise HubFailure(404, "CHAT_ATTACHMENT_NOT_FOUND", "This file is not attached to this conversation.")
            path = self._attachment_path(session_id, attachment)
            if not path.is_file():
                raise HubFailure(404, "CHAT_ATTACHMENT_NOT_FOUND", "The attached file is no longer available.")
            return attachment, path

    def read_attachment(self, session_id: str, attachment_id: str, *, offset: int = 0,
                        limit: int = 32768, page: int = 1) -> dict:
        _attachment_read_paging(offset, limit, page)
        attachment, path = self.attachment(session_id, attachment_id)
        total_pages = 1
        if attachment.mimeType == "application/pdf" or path.suffix == ".pdf":
            from pypdf import PdfReader

            try:
                with path.open("rb") as source:
                    reader = PdfReader(source)
                    total_pages = len(reader.pages)
                    if page > total_pages:
                        raise HubFailure(422, "CHAT_ATTACHMENT_PAGE_INVALID", f"This PDF has {total_pages} pages.")
                    text = reader.pages[page - 1].extract_text() or ""
            except HubFailure:
                raise
            except Exception as exc:
                raise HubFailure(422, "CHAT_ATTACHMENT_PDF_INVALID", "Text could not be extracted from this PDF.") from exc
            data = None
        else:
            if page != 1:
                raise HubFailure(422, "CHAT_ATTACHMENT_PAGE_INVALID", "Only PDF attachments have numbered pages.")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise HubFailure(503, "CHAT_ATTACHMENT_READ_FAILED", "The attached file could not be read.") from exc
            try:
                text = data.decode("utf-8") if b"\0" not in data else None
            except UnicodeDecodeError:
                text = None
        if text is not None:
            format_name, total, content = "text", len(text), text[offset:offset + limit]
        else:
            format_name, total = "base64", len(data)
            content = base64.b64encode(data[offset:offset + limit]).decode("ascii")
        return {**attachment.model_dump(), "format": format_name, "content": content,
                "offset": offset, "total": total, "nextOffset": offset + limit if offset + limit < total else None,
                "page": page, "totalPages": total_pages}

    def _save(self, session: _SavedChat, attachments: tuple[tuple[ChatAttachment, bytes], ...] = ()) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session.id}.json"
        temporary = path.with_suffix(".tmp")
        written = []
        saved = False
        try:
            for attachment, data in attachments:
                destination = self._attachment_path(session.id, attachment)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    written.append(destination)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(session.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            saved = True
            if self.on_change is not None:
                self.on_change(session)
        finally:
            temporary.unlink(missing_ok=True)
            if not saved:
                for destination in written:
                    destination.unlink(missing_ok=True)

    def _check(self, refresh: bool) -> None:
        """Ask the installed CLIs once, off the request thread.

        A check runs at most one at a time and is reused until it ages out, so
        a polling page never starts a CLI. Failure leaves the previous answer
        and never blocks the Hub.
        """
        with self._lock:
            fresh = self._checked_at is not None and time.monotonic() - self._checked_at < _CHECK_TTL_S
            if self._checking or self._closing or (fresh and not refresh):
                return
            self._checking = True
            commands = self.commands if self.commands is not None else _cli_commands()
            environment = _claude_env()

        def run() -> None:
            try:
                found = _check_providers(commands, environment)
            except Exception:  # noqa: BLE001 - a check never takes the Hub down
                found = {}
            with self._lock:
                if found:
                    self._checked = found
                    self._checked_at = time.monotonic()
                elif self._checked_at is None:
                    self._checked_at = time.monotonic()
                self._checking = False

        thread = threading.Thread(target=run, daemon=True, name="hub-chat-connections")
        with self._lock:
            self._check_thread = thread
        thread.start()

    def providers(self, refresh: bool = False) -> list[ChatProvider]:
        commands = self.commands if self.commands is not None else _cli_commands()
        env = _claude_env()
        configured_plan = bool(env.get("ANTHROPIC_BASE_URL") and (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")))
        self._check(refresh)
        with self._lock:
            checked, checking = dict(self._checked), self._checking and self._checked_at is None
        rows = [
            ChatProvider(id="codex", label="Codex CLI", available="codex" in commands, installed="codex" in commands,
                         detail="Uses the installed Codex CLI and its saved login." if "codex" in commands else "Codex CLI is not installed."),
            ChatProvider(id="claude", label="Claude Code", available="claude" in commands, installed="claude" in commands,
                         detail="Uses the installed Claude CLI and its saved login or configuration." if "claude" in commands else "Claude CLI is not installed."),
            ChatProvider(id="coding-plan", label="Coding Plan", available="claude" in commands and configured_plan,
                         installed="claude" in commands,
                         detail="Uses the Claude CLI's existing Anthropic-compatible endpoint and authentication." if configured_plan and "claude" in commands
                         else "No executable Coding Plan configuration was found. Configure the existing Claude CLI endpoint and authentication first."),
        ]
        if self._use_acp:
            codex = rows[0]
            codex.label = "Codex"
            if codex.installed and self._acp_command is None:
                codex.available = False
                codex.detail = "Install this Hub's locked ACP adapter with npm ci in apps/monkeyhub and its api/requirements.txt in the Hub Python environment."
            elif codex.installed:
                codex.detail = "Persistent ACP sessions through this Hub's adapter, using the installed Codex and its saved login."
        for row in rows:
            if not row.installed:
                row.modelCatalog, row.modelDetail = "unavailable", "Install this CLI to read the models it offers."
                continue
            answer = checked.get(row.id)
            if answer is None:
                row.modelCatalog = "checking" if checking else "unavailable"
                row.modelDetail = "" if checking else "This connection was not checked; try again."
                continue
            row.signedIn = answer.get("signedIn")
            row.models = list(answer.get("models") or [])
            row.modelCatalog = "ready" if row.models else "unavailable"
            row.modelDetail = str(answer.get("modelDetail") or "")
        return rows

    def set_model(self, session_id: str, model: str | None) -> ChatDetail:
        """Which model this conversation's next turn runs on.

        The conversation keeps its connection, its native CLI session and its
        project; only what the next start passes as the model changes. A turn
        already running keeps the model it started with.
        """
        with self._lock:
            session = self._session(session_id)
            if session_id in self._running:
                raise HubFailure(409, "CHAT_RUNNING", "Wait for this reply to finish before changing its model.")
            chosen = model.strip() if isinstance(model, str) else None
            if chosen == session.model or (not chosen and session.model is None):
                return self.get(session_id)
            session.model = chosen or None
            session.updatedAt = _now()
            self._save(session)
            return self.get(session_id)

    def projects(self) -> list[ChatProject]:
        with self._lock:
            self._load()
            projects: dict[tuple[str, str], ChatProject] = {}
            for session in self._sessions.values():
                key = (session.projectId, session.projectDir)
                if key not in projects:
                    version, stage = _position(key[1])
                    projects[key] = ChatProject(projectId=key[0], projectDir=key[1], name=key[0],
                                                chatCount=0, version=version, stage=stage)
                projects[key].chatCount += 1
            workspace = self.workspace()
            workspace_root = Path(workspace.workspaceDir).resolve()
            discovered = []
            for name in workspace.projects:
                try:
                    candidate = (workspace_root / name).resolve()
                    if candidate.is_relative_to(workspace_root):
                        discovered.append(str(candidate))
                except OSError:
                    continue
            current = read_application_settings(self.runtime_root).project_dir
            for path in dict.fromkeys([*discovered, *([current] if current else [])]):
                try:
                    project_id, project_dir = _project(path)
                except HubFailure:
                    pass
                else:
                    if (project_id, project_dir) not in projects:
                        version, stage = _position(project_dir)
                        projects[(project_id, project_dir)] = ChatProject(
                            projectId=project_id, projectDir=project_dir, name=project_id,
                            chatCount=0, version=version, stage=stage,
                        )
            return sorted(projects.values(), key=lambda row: (row.name, row.projectDir))

    def workspace(self) -> ChatWorkspace:
        """The folder new projects are created in, and the projects already there."""
        settings = read_application_settings(self.runtime_root)
        root = _workspace(self.runtime_root, settings)
        found: list[str] = []
        try:
            found = sorted(item.name for item in root.iterdir()
                           if item.is_dir() and (item / "project.json").is_file())
        except OSError:
            found = []
        return ChatWorkspace(workspaceDir=str(root), configured=bool(settings.workspace_dir), projects=found)

    def create_project(self, request: ChatProjectRequest) -> ChatProject:
        """Make an empty project in the workspace and list it as one."""
        with self._lock:
            self._load()
            settings = read_application_settings(self.runtime_root)
            workspace = Path(request.workspaceDir) if request.workspaceDir else _workspace(self.runtime_root, settings)
            root = _new_project(workspace, request.name)
            project_id, project_dir = _project(str(root))
            version, stage = _position(project_dir)
            return ChatProject(projectId=project_id, projectDir=project_dir, name=project_id,
                               chatCount=0, version=version, stage=stage)

    def list(self, project_id: str | None = None, *, archived: bool = False) -> list[ChatSummary]:
        # Polled by every open Hub view (#300): only the summary fields are
        # copied, never a whole transcript.
        with self._lock:
            self._load()
            return [ChatSummary.model_validate({**row.model_dump(include=_SUMMARY_FIELDS), "attention": _attention(row)})
                    for row in sorted(self._sessions.values(), key=lambda item: item.updatedAt, reverse=True)
                    if row.archived == archived and (project_id is None or row.projectId == project_id)]

    def usage_sources(self) -> list[ChatUsageSource]:
        """Archiving or starting fresh preserves every bound Codex usage source."""
        with self._lock:
            self._load()
            sources = []
            for row in self._sessions.values():
                if row.provider != "codex":
                    continue
                identifier = row.acpSessionId if row.transport == "acp" else row.nativeSessionId
                for identifier in dict.fromkeys([*row.priorProviderSessionIds, identifier]):
                    if identifier:
                        sources.append(ChatUsageSource(projectId=row.projectId, sessionId=identifier))
            return sources

    def _session(self, session_id: str) -> _SavedChat:
        self._load()
        _identifier(session_id)
        if session_id not in self._sessions:
            raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
        return self._sessions[session_id]

    def get(self, session_id: str) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            detail = ChatDetail.model_validate({**session.model_dump(), "attention": _attention(session)})
            extra = [row.model_copy(deep=True) for row in self._progress_rows.get(session_id, {}).values()]
            if detail.status != "running":
                ending = "interrupted" if detail.status == "interrupted" else "failed" if detail.status == "failed" else "complete"
                for row in extra:
                    if row.status == "streaming":
                        row.status = ending
            detail.messages = sorted([*detail.messages, *extra], key=lambda row: (row.createdAt, row.id))
            return detail

    def _progress(self, session, key: str, text: str, *, append: bool = False,
                  status: str = "complete") -> None:
        """One transient projection shared by native and external conversation events."""
        clean = _redact(text) if append else _redact(text).strip()
        if not clean:
            return
        rows = self._progress_rows.setdefault(session.id, {})
        identifier = f"{_turn_id(session)}:progress:{key}"
        row = rows.get(key)
        if row is None or row.id != identifier:
            row = ChatMessage(id=identifier, role="tool", content="", createdAt=_now(), status=status)
            rows[key] = row
        row.content = (row.content + clean if append else clean)[-2400:]
        row.status = status
        # This row is a live snapshot, not a retained transcript entry. Keep
        # its latest update beside the current activity as new tool rows arrive.
        row.createdAt = _now()
        if self.on_change is not None:
            self.on_change(session)

    def presentation_token(self, session_id: str) -> str:
        with self._lock:
            self._session(session_id)
            return self._presentation_tokens.setdefault(session_id, secrets.token_urlsafe(32))

    def bind_presentation(self, request: ChatPresentationBindRequest) -> ChatPresentationBinding:
        with self._lock:
            self._load()
            if self._closing:
                raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
            project_id, project_dir = _project(request.projectDir)
            if request.chatId:
                session = self._session(request.chatId)
            else:
                session = next((row for row in self._sessions.values()
                                if row.projectDir == project_dir and row.sourceSessionId == request.sourceSessionId), None)
            if session is None:
                now = _now()
                session = _SavedChat(id=str(uuid4()), projectId=project_id, projectDir=project_dir,
                                     title=request.title, provider=request.provider, sourceSessionId=request.sourceSessionId,
                                     createdAt=now, updatedAt=now)
                self._save(session)
                self._sessions[session.id] = session
            if (session.projectId, session.projectDir, session.sourceSessionId, session.provider) != (
                    project_id, project_dir, request.sourceSessionId, request.provider):
                raise HubFailure(409, "CHAT_PRESENTATION_MISMATCH", "Bind the original source session and project; a native chat cannot be adopted.")
            if session.archived:
                raise HubFailure(409, "CHAT_ARCHIVED", "Restore this chat in Hub before binding it.")
            if session.status == "interrupted" and session.error and session.error.code == "CHAT_INTERRUPTED":
                # An external agent can outlive Hub. Explicit rebind resumes that same in-flight turn.
                session = session.model_copy(update={"status": "running", "error": None, "updatedAt": _now()}, deep=True)
                self._save(session)
                self._sessions[session.id] = session
            return ChatPresentationBinding(chatId=session.id, projectId=project_id, sourceSessionId=request.sourceSessionId,
                                           token=self.presentation_token(session.id),
                                           url=self.hub_url + "/?" + urlencode({"chatId": session.id}))

    def _document(self, session, ref: ChatDocumentRef):
        from archflow_studio_api.application.artifacts import document_bytes, list_documents
        from archflow_studio_api.application.binding import ProjectBinding
        from archflow_studio_api.settings import StudioSettings

        project_id, project_dir = _project(session.projectDir)
        if (project_id, project_dir) != (session.projectId, session.projectDir):
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
        root = Path(project_dir)
        binding = ProjectBinding(FilesystemProjectRepository.open(root), project_id=project_id, project_dir=root,
                                 settings=StudioSettings(project_dir=root, cad_export="off"))
        document = next((row for row in list_documents(binding, ref.runId)
                         if (row.run_id, row.asset_sha256, row.revision_ref) == (ref.runId, ref.assetSha256, ref.revisionRef)), None)
        if document is None or ref.pageIndex not in {page.page_index for page in document.pages}:
            raise HubFailure(409, "CHAT_DOCUMENT_MISMATCH", "This exact document revision or page is unavailable.")
        # The legacy byte reader treats null revision as a wildcard. Select exact metadata first;
        # bytes remain content-addressed and independently verified by their existing owner.
        _, data = document_bytes(binding, ref.runId, ref.assetSha256, ref.revisionRef)
        return document, data

    def presentation_document(self, session_id: str, message_id: str, index: int):
        with self._lock:
            session = self._session(session_id)
            message = next((row for row in session.messages if row.id == message_id), None)
            if message is None or not 0 <= index < len(message.documents):
                raise HubFailure(404, "CHAT_DOCUMENT_NOT_FOUND", "This document is not in this conversation.")
            return self._document(session, message.documents[index])

    def present(self, session_id: str, request: ChatPresentationRequest, token: str) -> ChatDetail:
        """Upsert a public result without starting a provider or writing project state."""
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            expected = self._presentation_tokens.get(session_id)
            if not expected or not secrets.compare_digest(expected, token):
                raise HubFailure(403, "CHAT_PRESENTATION_BIND_REQUIRED", "Reconnect the bound presentation tool to this Hub.")
            if self._closing or session.archived:
                raise HubFailure(409, "CHAT_UNAVAILABLE", "Restore the conversation or reconnect after Hub restarts.")
            if (request.projectId != session.projectId or request.sourceSessionId != (session.sourceSessionId or f"hub:{session_id}")
                    or _project(session.projectDir) != (session.projectId, session.projectDir)):
                raise HubFailure(409, "CHAT_PRESENTATION_MISMATCH", "The presentation belongs to a different project or source session.")
            _identifier(request.messageId)
            _identifier(request.turnId)
            if request.kind == "user" and (not session.sourceSessionId or request.status != "complete"):
                raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Only an external source can start a turn with a complete user message.")
            if request.kind == "progress" and (request.attachments or request.documents):
                raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Progress is transient text; publish media as an assistant result.")
            if not request.content.strip() and not request.attachments and not request.documents:
                raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Provide text or a result attachment/document.")
            progress_key = f"external:{request.messageId}"
            previous = (self._progress_rows.get(session_id, {}).get(progress_key) if request.kind == "progress" else
                        next((row for row in session.messages if row.id == request.messageId), None))
            content = _redact(request.content, _claude_env())
            if request.kind == "progress":
                content = content.strip()[-2400:]
            if previous:
                role = "tool" if request.kind == "progress" else request.kind
                if previous.sourceTurnId != request.turnId or previous.role != role or previous.presentationRevision is None:
                    raise HubFailure(409, "CHAT_PRESENTATION_CONFLICT", "This message id belongs to another item or turn.")
                if request.revision < previous.presentationRevision:
                    raise HubFailure(409, "CHAT_PRESENTATION_STALE", "An older presentation revision cannot replace this result.")
                if request.revision == previous.presentationRevision:
                    same_files = len(request.attachments) == len(previous.attachments) and all(
                        upload.name == stored.name and upload.mimeType == stored.mimeType and
                        upload.data == base64.b64encode(self._attachment_path(session_id, stored).read_bytes()).decode("ascii")
                        for upload, stored in zip(request.attachments, previous.attachments))
                    same_refs = [ref.model_dump() for ref in request.documents] == [
                        ChatDocumentRef.model_validate(ref.model_dump(include=set(ChatDocumentRef.model_fields))).model_dump()
                        for ref in previous.documents]
                    settled_stream = request.status == "streaming" and previous.status != "streaming" and session.status != "running"
                    if previous.content == content and (previous.status == request.status or settled_stream) and same_files and same_refs:
                        return self.get(session_id)
                    raise HubFailure(409, "CHAT_PRESENTATION_CONFLICT", "A revision identifies one exact message snapshot.")
                if previous.status != "streaming" or request.kind == "user":
                    raise HubFailure(409, "CHAT_PRESENTATION_COMPLETE", "A completed message cannot be rewritten.")
            current_user = next((row for row in reversed(session.messages) if row.role == "user"), None)
            current_turn = current_user.sourceTurnId or current_user.id if current_user else None
            if request.kind == "user":
                if session.status == "running" or any(row.sourceTurnId == request.turnId for row in session.messages):
                    raise HubFailure(409, "CHAT_RUNNING", "Finish the active turn and use a new turn id for the next request.")
            elif session.status != "running" or current_turn != request.turnId:
                raise HubFailure(409, "CHAT_PRESENTATION_TURN_CLOSED", "Start a user turn or use the currently running turn id.")
            if request.kind == "progress":
                self._progress(session, progress_key, content, status=request.status)
                row = self._progress_rows[session_id][progress_key]
                row.sourceTurnId, row.presentationRevision = request.turnId, request.revision
                return self.get(session_id)
            attachments, writes = [], []
            total = 0
            for index, upload in enumerate(request.attachments):
                try:
                    data = base64.b64decode(upload.data, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "The attached file could not be decoded.") from exc
                total += len(data)
                if len(data) > 20 * 1024 * 1024 or total > 40 * 1024 * 1024:
                    raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Files are limited to 20 MiB each and 40 MiB per message.")
                if upload.mimeType in _IMAGE_MIMES:
                    _verify_image(data, upload.mimeType)
                old = previous.attachments[index] if previous and index < len(previous.attachments) else None
                if old:
                    if (old.name, old.mimeType) != (upload.name, upload.mimeType) or self._attachment_path(session_id, old).read_bytes() != data:
                        raise HubFailure(409, "CHAT_ATTACHMENT_CONFLICT", "Publish a changed image as a new message; existing attachments stay exact.")
                    attachment = old
                else:
                    attachment = ChatAttachment(id=str(uuid4()), name=upload.name, mimeType=upload.mimeType, size=len(data))
                    writes.append((attachment, data))
                attachments.append(attachment)
            if previous and len(attachments) < len(previous.attachments):
                raise HubFailure(409, "CHAT_ATTACHMENT_CONFLICT", "An update must retain existing attachments.")
            documents = []
            for ref in request.documents:
                document, _ = self._document(session, ref)
                documents.append(ChatDocument(**ref.model_dump(), fileName=document.file_name, mimeType=document.mime_type))
            message = ChatMessage(id=request.messageId, role=request.kind, content=content,
                                  createdAt=previous.createdAt if previous else _now(), status=request.status,
                                  sourceTurnId=request.turnId, presentationRevision=request.revision,
                                  attachments=attachments, documents=documents)
            if previous:
                session.messages[session.messages.index(previous)] = message
            else:
                session.messages.append(message)
            if request.kind == "user":
                session.status, session.error = "running", None
            elif session.sourceSessionId and request.status != "streaming":
                session.status = "idle" if request.status == "complete" else request.status
                for row in session.messages:
                    if row.sourceTurnId == request.turnId and row.status == "streaming":
                        row.status = request.status
            session.updatedAt = _now()
            self._save(session, tuple(writes))
            self._sessions[session_id] = session
            if request.kind == "user":
                self._progress_rows.pop(session_id, None)
            return self.get(session_id)

    def set_archived(self, session_id: str, archived: bool) -> ChatDetail:
        """Hide or restore a conversation without changing its native session."""
        with self._lock:
            session = self._session(session_id)
            if session_id in self._running or session.status == "running":
                raise HubFailure(409, "CHAT_RUNNING", "Wait for this reply to finish or stop it before archiving the chat.")
            if session.archived == archived:
                return self.get(session_id)
            updated = session.model_copy(update={"archived": archived, "updatedAt": _now()}, deep=True)
            self._save(updated)
            self._sessions[session_id] = updated
            return self.get(session_id)

    def create(self, request: ChatCreateRequest) -> ChatDetail:
        with self._lock:
            self._load()
            provider = next(row for row in self.providers() if row.id == request.provider)
            if not provider.available:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE", provider.detail)
            project_id, project_dir = _project(request.projectDir)
            now = _now()
            session = _SavedChat(
                id=str(uuid4()), projectId=project_id, projectDir=project_dir,
                title=request.title or "New chat", provider=request.provider, model=request.model,
                createdAt=now, updatedAt=now,
                transport="acp" if self._use_acp and request.provider == "codex" else "cli",
            )
            self._save(session)
            self._sessions[session.id] = session
            return self.get(session.id)

    @contextmanager
    def project_configuration(self, project_dir: str | None):
        """Hold the same lock across configuration changes and turn admission."""
        with self._lock:
            target = str(Path(project_dir).resolve()) if project_dir else None
            if any(self._sessions[key].projectDir != target for key in self._running):
                raise HubFailure(409, "CHAT_PROJECT_BUSY", "Stop the running chat before switching the active project.")
            yield

    @contextmanager
    def application_lifecycle(self, app_id: str, *, stopping: bool = False, project_dir: str | None = None):
        with self._lock:
            if app_id in {"monkeyarch", "monkeyboard"} and stopping:
                configured = project_dir if project_dir is not None else read_application_settings(self.runtime_root).project_dir
                target = str(Path(configured).resolve()) if configured else None
                if any(target is not None and os.path.normcase(self._sessions[key].projectDir) == os.path.normcase(target) for key in self._running):
                    raise HubFailure(409, "CHAT_RUNNING", "Stop the running chat before closing its design tools.")
            yield

    def post(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            if session.sourceSessionId:
                raise HubFailure(409, "CHAT_EXTERNAL_SOURCE", "Continue this conversation in its external source; Hub only displays its results.")
            if self._closing:
                raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
            if session.archived:
                raise HubFailure(409, "CHAT_ARCHIVED", "Restore this archived chat before sending another message.")
            if request.projectId != session.projectId or _project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This message belongs to a different project.")
            if session_id in self._running:
                return self._interject(session_id, request)
            provider = next(row for row in self.providers() if row.id == session.provider)
            legacy_codex = session.provider == "codex" and session.transport == "cli" and provider.installed
            if not provider.available and not legacy_codex:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE", provider.detail)
            content = _redact(request.content.strip(), _claude_env())
            if not content and not request.attachments:
                raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Enter a message or attach a file.")
            attachments = []
            total = 0
            for upload in request.attachments:
                try:
                    data = base64.b64decode(upload.data, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "The attached file could not be decoded.") from exc
                total += len(data)
                if len(data) > 20 * 1024 * 1024 or total > 40 * 1024 * 1024:
                    raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Files are limited to 20 MiB each and 40 MiB per message.")
                attachments.append((ChatAttachment(id=str(uuid4()), name=upload.name, mimeType=upload.mimeType, size=len(data)), data))
            if not session.messages and session.title == "New chat":
                session.title = (content.splitlines()[0] if content else attachments[0][0].name)[:80]
            session.messages.append(ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=_now(),
                                                contextMode="continue" if request.contextMode == "stage" else request.contextMode,
                                                attachments=[attachment for attachment, _ in attachments]))
            session.status, session.error, session.updatedAt = "running", None, _now()
            self._save(session, tuple(attachments))
            self._sessions[session_id] = session
            self._progress_rows.pop(session_id, None)
            running = _Running(design_context=request.designContext, context_mode=request.contextMode,
                               attachments=tuple((attachment, self._attachment_path(session.id, attachment))
                                                 for attachment, _ in attachments), trace=HubTurnObserver(
                self.usage_log, _turn_id(session), session.projectId, session.provider, session.model,
            ))
            self._start(session_id, running, content)
            return self.get(session_id)

    def _start(self, session_id: str, running: _Running, content: str, carried: tuple[str, ...] = (),
               earlier: tuple[str, ...] = ()) -> None:
        """Register one turn and start its thread; the caller holds the lock.

        A continuation also knows the turns before it, so a call they opened
        and that only reports its end now keeps its own row.
        """
        session = self._sessions[session_id]
        running.turns = [*earlier, *carried] or [_turn_id(session)]
        if session.transport == "cli" and session.provider != "codex":
            # Claude keeps reading stream-json user messages until its result.
            running.stdin = queue.Queue()
        self._running[session_id] = running
        running.thread = threading.Thread(target=self._run, args=(session_id, content, running, carried),
                                          daemon=True, name=f"hub-chat-{session_id[:8]}")
        running.thread.start()

    def _interject(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        """Take a message sent while this chat's turn runs (#301); the lock is held.

        It is kept like any other message and marked as an interjection. Claude
        reads it from the stdin of its running turn at its next step. Codex takes
        it through the adapter's steering when the adapter offers that, and
        otherwise the current step is cancelled and the turn continues with it
        as the next prompt. Whatever the running turn can no longer take becomes
        the next prompt when it ends; nothing is refused for being early.
        """
        running = self._running[session_id]
        if request.attachments:
            raise HubFailure(409, "CHAT_INTERJECTION_FILES", "Attach files after this reply finishes, or stop it first.")
        content = _redact(request.content.strip(), _claude_env())
        if not content:
            raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Enter a message or attach a file.")
        # The running turn holds this same record and saves it as it goes, so
        # the message joins it in place rather than through a replacement copy.
        session = self._sessions[session_id]
        message = ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=_now(), interjection="pending")
        session.messages.append(message)
        session.updatedAt = _now()
        try:
            self._save(session)
        except BaseException:
            session.messages.remove(message)
            raise
        cancel = None
        if running.stdin is not None:
            running.stdin.put(json.dumps({
                "type": "user", "session_id": session.nativeSessionId or session.cliStartId or session.id,
                "parent_tool_use_id": None, "uuid": message.id,
                "message": {"role": "user", "content": [{"type": "text", "text": content}]},
            }, ensure_ascii=False) + "\n")
            running.written.append(message.id)
        else:
            running.waiting.append((message.id, content))
            # A Codex CLI turn reads its prompt once and nothing after it.
            fixed = (session.transport == "cli" and session.provider == "codex"
                     and running.process is not None and running.process.poll() is None)
            if running.redirected:
                # The step is already being cancelled; this one follows it.
                message.interjection = "restarted"
                self._save(session)
            elif session.transport == "acp" and running.prompting:
                # Whether the adapter steers is known once its turn is under way;
                # the delivery thread waits for that and then steers or cancels.
                threading.Thread(target=self._steer, args=(session_id, running), daemon=True,
                                 name=f"hub-steer-{session_id[:8]}").start()
            elif fixed:
                cancel = self._redirect(session_id, running)
            # Otherwise the provider has not started yet, and its prompt takes
            # the message, or it has finished reading, and the next one does.
        detail = self.get(session_id)
        if cancel is not None:
            # Cancelling waits on the adapter's thread or on a process, which may
            # in turn wait for this store's lock, held here by the request.
            threading.Thread(target=cancel, daemon=True, name=f"hub-redirect-{session_id[:8]}").start()
        return detail

    def _redirect(self, session_id: str, running: _Running):
        """Stop the current step so the turn continues with its interjections.

        The lock is held. Work already reported stays in the conversation; only
        a permission still waiting is withdrawn, because the step it belonged to
        is ending. Returns what cancels the step, to call outside the lock.
        """
        running.redirected = True
        session = self._sessions[session_id]
        waiting = {identifier for identifier, _ in running.waiting}
        for message in session.messages:
            if message.id in waiting:
                message.interjection = "restarted"
        self._clear_permissions(session_id)
        self._save(session)
        client, process = self._acp_sessions.get(session_id), running.process
        if session.transport == "acp" and client is not None:
            return client.cancel
        if process is not None:
            return lambda: _stop_process(process)
        return None

    def _steer(self, session_id: str, running: _Running) -> None:
        """Offer this turn's waiting interjections to the adapter, one at a time and in order."""
        with running.steering:
            while True:
                with self._lock:
                    if (self._running.get(session_id) is not running or running.stop.is_set()
                            or running.redirected or not running.prompting or not running.waiting):
                        return
                    identifier, content = running.waiting[0]
                    client = self._acp_sessions.get(session_id)
                if client is None:
                    return
                outcome = client.steer(content, lambda identifier=identifier: self._handing(session_id, running, identifier))
                if outcome == "startedNewTurn":
                    # The turn ended as the message arrived and the adapter began
                    # a turn of its own for it, which no prompt of this Hub waits
                    # on. That turn is cancelled; the message goes as a prompt.
                    client.cancel_turn()
                cancel = None
                with self._lock:
                    session = self._sessions[session_id]
                    queued = any(waiting == identifier for waiting, _ in running.waiting)
                    running.waiting = [row for row in running.waiting if row[0] != identifier]
                    if outcome == "injected":
                        self._delivered(session, identifier)
                        continue
                    if not queued:
                        # The turn's end took it for the next prompt before it left.
                        return
                    if identifier in running.turns:
                        running.turns.remove(identifier)
                    message = next((row for row in session.messages if row.id == identifier), None)
                    if message is None or message.interjection == "undelivered":
                        # The turn already ended stopped or failed, and said so.
                        return
                    current = self._running.get(session_id)
                    message.interjection = "undelivered" if running.stop.is_set() or self._closing else "pending"
                    self._save(session)
                    if message.interjection == "undelivered":
                        return
                    if current is None:
                        # This turn has already ended: the message is the next prompt.
                        session.status, session.error = "running", None
                        self._save(session)
                        self._continue(session_id, [(identifier, content)], redirected=False,
                                       earlier=tuple(running.turns))
                        return
                    if current is not running:
                        # A later turn runs now; it takes the message as its own.
                        current.waiting.append((identifier, content))
                        if current.prompting:
                            threading.Thread(target=self._steer, args=(session_id, current), daemon=True,
                                             name=f"hub-steer-{session_id[:8]}").start()
                        return
                    # Still this turn: the message waits for its next prompt, or,
                    # without steering, the current step stops for it now.
                    running.waiting.insert(0, (identifier, content))
                    if outcome in {"failed", "unsupported"} and running.prompting and not running.redirected:
                        cancel = self._redirect(session_id, running)
                if cancel is not None:
                    cancel()
                return

    def _handing(self, session_id: str, running: _Running, identifier: str) -> bool:
        """Called on the adapter's thread as a steer is about to leave.

        From here what the Agent says answers the message. A turn that has
        already ended, or stopped, or taken the message for its next prompt,
        keeps it from leaving at all, so it can never arrive twice.
        """
        with self._lock:
            if (self._running.get(session_id) is not running or running.stop.is_set() or running.redirected
                    or not any(waiting == identifier for waiting, _ in running.waiting)):
                return False
            self._delivered(self._sessions[session_id], identifier)
            return True

    def _delivered(self, session: _SavedChat, identifier: str) -> None:
        """The Agent has this interjection; what it says next answers it. The lock is held."""
        message = next((row for row in session.messages if row.id == identifier and row.role == "user"), None)
        if message is None or message.interjection not in {"pending", "undelivered"}:
            return
        message.interjection = "delivered"
        running = self._running.get(session.id)
        if running is not None:
            if identifier in running.written:
                running.written.remove(identifier)
            if identifier not in running.turns:
                running.turns.append(identifier)
        session.updatedAt = _now()
        self._save(session)

    def _answering(self, session: _SavedChat) -> str:
        """The user message the running turn is answering: the last one the Agent has.

        An interjection counts from the moment it is handed over; until then,
        and for a step stopped on its account, what the Agent says still
        belongs to the message before it.
        """
        running = self._running.get(session.id)
        return running.turns[-1] if running is not None and running.turns else _turn_id(session)

    def _call_row(self, session: _SavedChat, call: str) -> str:
        """The row of one tool call: where it was opened, else under the current turn."""
        running = self._running.get(session.id)
        for turn in reversed(running.turns if running is not None else []):
            identifier = f"{turn}:{call}"
            if any(row.id == identifier for row in session.messages):
                return identifier
        return f"{self._answering(session)}:{call}"

    def _take_waiting(self, session_id: str, running: _Running, prompt: str) -> str:
        """Fold interjections sent before the provider started into its prompt. The lock is held."""
        if not running.waiting:
            return prompt
        session = self._sessions[session_id]
        for identifier, content in running.waiting:
            prompt += "\n\n" + content
            self._delivered(session, identifier)
        running.waiting.clear()
        return prompt

    def _tool_connection(self, session: _SavedChat) -> dict:
        return {"command": sys.executable, "args": [str(Path(__file__).resolve()), "--mcp",
                "--hub-url", self.hub_url, "--chat-id", session.id]}

    def _codex_mcp(self, session: _SavedChat, command, environment) -> dict:
        """Keep the same project-only MCP configuration for both native transports."""
        mcp = self._tool_connection(session)
        listing = subprocess.run(
            [*command, "-C", session.projectDir, "mcp", "list", "--json"],
            cwd=session.projectDir, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            servers = json.loads(listing.stdout)
            if listing.returncode or not isinstance(servers, list):
                raise ValueError("invalid MCP listing")
            mcp_servers = {}
            for row in servers:
                kind_name = row["transport"]["type"]
                if kind_name == "stdio":
                    transport = mcp
                elif kind_name == "streamable_http":
                    transport = {"url": self.hub_url}
                else:
                    raise ValueError("unsupported MCP transport")
                mcp_servers[row["name"]] = {**transport, "enabled": False, "required": False}
        except (ValueError, KeyError, TypeError) as exc:
            raise HubFailure(503, "CHAT_CONFIG_INVALID", "The installed Codex MCP configuration could not be read.") from exc
        from . import computer_tools

        tool_names = ("studio_schema", "studio_request", "visual_review", "fab_request", "attachment_read",
                      "chat_present", *computer_tools.TOOL_NAMES)
        mcp_servers["monkeyhub"] = {
            **mcp, "enabled": True, "required": True,
            "env_vars": ["MONKEYHUB_PRESENTATION_TOKEN"],
            "enabled_tools": list(tool_names),
            "tools": {name: {"approval_mode": "approve"} for name in tool_names},
        }
        return mcp_servers

    def _command(self, session: _SavedChat, attachments: tuple[tuple[ChatAttachment, Path], ...] = ()) -> tuple[list[str], dict[str, str]]:
        commands = self.commands if self.commands is not None else _cli_commands()
        kind = "codex" if session.provider == "codex" else "claude"
        environment = _claude_env() if kind == "claude" else dict(os.environ)
        environment["MONKEYHUB_PRESENTATION_TOKEN"] = self.presentation_token(session.id)
        mcp = self._tool_connection(session)
        model = session.model
        # The assistant works where the work is: this Hub's own source tree when
        # it is a checkout, with the bound project writable beside it, so a
        # candidate can be produced and the code that produced it can be fixed.
        source = _source_checkout()
        workdir = str(source) if source is not None else session.projectDir
        if kind == "codex":
            # Let Codex read its native profile, provider and credentials.
            # Its table overrides merge, so disable the effective MCP names
            # explicitly. Never copy credential-bearing config into argv.
            mcp_servers = self._codex_mcp(session, commands[kind], environment)
            command = [*commands[kind], "exec", "--json", "--skip-git-repo-check",
                       "-C", workdir, "-s", "workspace-write", "--color", "never",
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       "-c", f"mcp_servers={_toml_value(mcp_servers)}"]
            if model:
                command += ["-m", model]
            if session.nativeSessionId:
                command += ["resume", session.nativeSessionId]
            for attachment, path in attachments:
                if attachment.mimeType in _IMAGE_MIMES:
                    command += ["--image", str(path)]
            command.append("-")
        else:
            command = [*commands[kind], "-p", "--output-format", "stream-json", "--verbose",
                       "--include-partial-messages", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                       # Two different questions, and both have to be answered.
                       # `--tools` is what exists; `--allowedTools` is what is
                       # approved without anybody to ask, which is the whole of
                       # it with no prompt attached. Named rather than bypassed:
                       # reading, editing and running in the workspace above,
                       # plus this adapter's own tools and nothing else.
                       "--tools", "default", "--allowedTools", ",".join(_claude_approved(self.runtime_root)),
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"monkeyhub": mcp}})]
            command += ["--resume", session.nativeSessionId] if session.nativeSessionId else ["--session-id", session.cliStartId or session.id]
            # Every turn reads stream-json from stdin, so a message sent while
            # it runs reaches it at its next step (#301). Each one read is
            # echoed back, which is how the conversation knows it arrived.
            command += ["--input-format", "stream-json", "--replay-user-messages"]
            if model:
                command += ["--model", model]
        return command, environment

    def _acp_permission(self, session_id: str, request: dict) -> Future:
        future = Future()
        with self._lock:
            session = self._session(session_id)
            running = self._running.get(session_id)
            if (self._closing or running is None or running.stop.is_set() or running.redirected
                    or request.get("sessionId") != session.acpSessionId):
                future.set_result(None)
                return future
            permission = ChatPermission(
                id=str(uuid4()), title=_redact(str(request.get("toolCall", {}).get("title") or "Permission requested")),
                options=[ChatPermissionOption.model_validate(item) for item in request["options"]],
            )
            session.messages.append(ChatMessage(
                id=f"{self._answering(session)}:permission:{permission.id}", role="tool", content=permission.title,
                createdAt=_now(), status="streaming", permission=permission,
            ))
            # A request is a change of its own (#300): two requests in a row are
            # two moments in the summary, never one.
            session.updatedAt = _now()
            self._permissions[(session_id, permission.id)] = future
            if running.trace:
                running.trace.permission(permission.id)
            self._save(session)
        return future

    def resolve_permission(self, session_id: str, permission_id: str, request: ChatPermissionRequest) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            if request.projectId != session.projectId or _project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This permission belongs to a different project.")
            future = self._permissions.get((session_id, permission_id))
            message = next((row for row in session.messages if row.permission and row.permission.id == permission_id), None)
            if future is None or future.done() or message is None or session_id not in self._running:
                raise HubFailure(409, "CHAT_PERMISSION_EXPIRED", "This permission request is no longer waiting for a decision.")
            option = next((item for item in message.permission.options if item.optionId == request.optionId), None)
            if request.optionId is not None and option is None:
                raise HubFailure(422, "CHAT_PERMISSION_OPTION_INVALID", "Choose an option from this permission request.")
            try:
                future.set_result(request.optionId)
            except InvalidStateError:
                message.permission, message.status = None, "interrupted"
                session.updatedAt = _now()
                self._permissions.pop((session_id, permission_id), None)
                self._save(session)
                raise HubFailure(409, "CHAT_PERMISSION_EXPIRED", "This permission request is no longer waiting for a decision.") from None
            message.content += " · " + (option.name if option else "Cancelled")
            active = self._running[session_id]
            if active.trace:
                active.trace.permission(permission_id, completed=True)
            message.permission, message.status = None, "complete"
            session.updatedAt = _now()
            self._save(session)
            self._permissions.pop((session_id, permission_id))
            return self.get(session_id)

    def _clear_permissions(self, session_id: str) -> None:
        for key, future in list(self._permissions.items()):
            if key[0] == session_id:
                self._permissions.pop(key)
                with suppress(InvalidStateError):
                    future.set_result(None)
        for message in self._sessions[session_id].messages:
            if message.permission is not None:
                message.permission, message.status = None, "interrupted"

    def _acp_update(self, session_id: str, event: dict, environment: dict) -> None:
        with self._lock:
            session = self._session(session_id)
            running = self._running.get(session_id)
            if running is None or running.stop.is_set() or event.get("sessionId") != session.acpSessionId:
                return
            update = event["update"]
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                if running.trace and update["content"].get("text"):
                    running.trace.first_response()
                identifier = f"{self._answering(session)}:acp-answer"
                message = next((row for row in session.messages if row.id == identifier), None)
                if message is None:
                    message = ChatMessage(id=identifier, role="assistant", content="", createdAt=_now(), status="streaming")
                    session.messages.append(message)
                message.content += _redact(update["content"]["text"], environment)
            elif kind in {"tool_call", "tool_call_update"}:
                call_id = update["toolCallId"]
                call = self._acp_tools.setdefault(session_id, {}).setdefault(call_id, {})
                call.update(update)
                raw_input = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
                raw_output = call.get("rawOutput") if isinstance(call.get("rawOutput"), dict) else {}
                mcp = call.get("_meta", {}).get("is_mcp_tool_call") is True
                item = {
                    "id": call_id, "server": raw_input.get("server") if mcp else None,
                    "tool": raw_input.get("tool") if mcp else call.get("title", "tool"),
                    "arguments": raw_input.get("arguments") if mcp else raw_input,
                    "status": call.get("status", "in_progress"),
                    "result": raw_output.get("result") if mcp else call.get("rawOutput"),
                    "error": raw_output.get("error") if mcp else None,
                }
                self._tool_message(session, item, "item.started" if item["status"] in {"pending", "in_progress"} else "item.completed", environment)
            else:
                return
            session.updatedAt = _now()
            now = time.monotonic()
            if now - running.last_save >= 0.3:
                self._save(session)
                running.last_save = now

    def _run_acp(self, session_id: str, prompt: str, running: _Running) -> HubError | None:
        from .acp_session import AcpCancelled, CodexAcpSession

        environment = dict(os.environ)
        environment["MONKEYHUB_PRESENTATION_TOKEN"] = self.presentation_token(session_id)
        try:
            with self._lock:
                session = self._sessions[session_id]
                client = self._acp_sessions.get(session_id)
                # A continuation keeps what the cancelled step's calls said so far.
                self._acp_tools.setdefault(session_id, {})
            if client is None:
                commands = self.commands if self.commands is not None else _cli_commands()
                environment["CODEX_PATH"] = _native_codex(commands["codex"])
                # In this pinned adapter, read-only means workspace-write with
                # user approvals. Its default agent mode uses auto-review.
                environment["INITIAL_AGENT_MODE"] = "read-only"
                environment["CODEX_CONFIG"] = json.dumps({
                    "mcp_servers": self._codex_mcp(session, commands["codex"], environment),
                    "sandbox_workspace_write": {"writable_roots": [session.projectDir]},
                })
                client = CodexAcpSession(
                    command=self._acp_command, cwd=str(_source_checkout() or session.projectDir),
                    environment=environment,
                    # Nonempty ACP mcpServers would replace the disabled entries
                    # above. This per-chat adapter already has the exact binding.
                    mcp_servers=[], default_model=session.acpDefaultModel,
                    on_update=lambda event: self._acp_update(session_id, event, environment),
                    on_permission=lambda request: self._acp_permission(session_id, request),
                )
                with self._lock:
                    self._acp_sessions[session_id] = client
            if running.stop.is_set():
                raise AcpCancelled("The chat was stopped before its turn was sent.")

            def connected(identifier: str) -> None:
                with self._lock:
                    session.acpSessionId = identifier
                    session.acpDefaultModel = client.default_model
                    if running.trace:
                        running.trace.bind(identifier, session.model or client.default_model)
                    self._save(session)

            # This adapter's own limit is an inactivity interval that its updates
            # reschedule, not a total for the turn. It keeps the whole of it.
            images = tuple((attachment.mimeType, base64.b64encode(path.read_bytes()).decode("ascii"))
                           for attachment, path in running.attachments if attachment.mimeType in _IMAGE_MIMES)
            with self._lock:
                # What was sent before the prompt left goes with it; what is
                # sent after is steered into it or continues after it.
                prompt = self._take_waiting(session_id, running, prompt)
                running.prompting = True
            try:
                client.prompt(prompt, session.acpSessionId, session.model, connected, self.timeout_s, images=images)
            finally:
                with self._lock:
                    running.prompting = False
            return None
        except AcpCancelled:
            if not running.redirected:
                running.stop.set()
            return None
        except Exception as exc:
            with self._lock:
                client = self._acp_sessions.pop(session_id, None)
            if client is not None:
                client.close()
            return HubError(code="CHAT_ACP_FAILED", detail=_redact(str(exc), environment)[:1000])

    def _fresh_provider_session(self, session_id: str, *, stage: dict | None = None,
                                automatic: bool = False) -> _SavedChat:
        """Replace provider continuity only after this turn's source was verified."""
        with self._lock:
            session = self._sessions[session_id].model_copy(deep=True)
            identifier = session.acpSessionId if session.transport == "acp" else session.nativeSessionId
            if identifier and identifier not in session.priorProviderSessionIds:
                session.priorProviderSessionIds.append(identifier)
            session.nativeSessionId = None
            session.acpSessionId = None
            # Claude requires a fresh UUID even before it reports a native ID.
            session.cliStartId = str(uuid4())
            # A manual reset replaces the provider, not the boundary already
            # handled: without a named Stage the recorded one still stands, so
            # returning to it does not rotate the provider a second time.
            if stage is not None:
                session.providerStageRef = stage["stageRef"]
            if automatic:
                message = _asked(session)
                message.contextMode = "stage"
                message.confirmedStageRef = stage["stageRef"]
                message.confirmedStageLabel = stage["label"]
            self._save(session)
            self._sessions[session_id] = session
            client = self._acp_sessions.pop(session_id, None)
        if client is not None:
            client.close()
        return session

    def _retained_continuity(self, session: _SavedChat) -> dict:
        """What a rotation is about to replace, kept in case nothing replaces it."""
        boundary = _asked(session)
        return {
            "session": {"nativeSessionId": session.nativeSessionId, "acpSessionId": session.acpSessionId,
                        "cliStartId": session.cliStartId, "acpDefaultModel": session.acpDefaultModel,
                        "providerStageRef": session.providerStageRef,
                        "priorProviderSessionIds": list(session.priorProviderSessionIds)},
            "message": None if boundary is None else (boundary.id, {
                "contextMode": boundary.contextMode, "confirmedStageRef": boundary.confirmedStageRef,
                "confirmedStageLabel": boundary.confirmedStageLabel}),
        }

    def _restore_continuity(self, session: _SavedChat, retained: dict):
        """Put back continuity a replacement took away and then never replaced.

        Only what the rotation itself wrote is undone: this turn's messages,
        outcome and error are its own and stay exactly as they happened.
        """
        for field, value in retained["session"].items():
            setattr(session, field, value)
        if retained["message"] is not None:
            identifier, marks = retained["message"]
            message = next((row for row in session.messages if row.id == identifier), None)
            if message is not None:
                for field, value in marks.items():
                    setattr(message, field, value)
        # The adapter opened for the replacement negotiated no session of its
        # own, and it cannot be asked to load the restored one: it is given up
        # here, and the next turn loads that identity on an adapter of its own.
        return self._acp_sessions.pop(session.id, None)

    def _run(self, session_id: str, content: str, running: _Running, carried: tuple[str, ...] = ()) -> None:
        error: HubError | None = None
        retained: dict | None = None
        stderr: list[str] = []
        completed_turn = False
        # When this turn's own limit is spent. Preparing a named source is bound
        # by it and cancellable within it, and the CLI transport — which holds
        # one total limit for the turn — gets what preparing left rather than a
        # fresh limit beside it. The ACP adapter keeps its own inactivity
        # interval, which this is not and cannot stand in for.
        deadline = time.monotonic() + self.timeout_s
        try:
            with self._lock:
                session = self._sessions[session_id]
                prompt = (
                    "You are the assistant in MonkeyHub. Reply in the user's language. "
                    "Develop the user's design through reversible candidates. Batch steps whose outcome is already clear; "
                    "generate and inspect a candidate when the next design decision depends on its result, then revise as needed. "
                    "Check the actual result against the user's spatial intent before reporting completion; distinguish "
                    "geometry readback from visual inspection. Judge a spatial or formal result with visual_review, which "
                    "answers findings rather than images within a small allowance for each user message; check a "
                    "deterministic edit by readback without looking, and ask the user about a finding marked escalate "
                    "rather than spending another review on it. Close each completed loop with one admission that lists the "
                    "attempts each result superseded, such as a redone first try; for a request for several alternatives, "
                    "declare its Study with an id and label from the request and admit each finished alternative. Never "
                    "admit intermediate runs. Reject a result, or continue from one, only when the user's own words say so; "
                    "the chat binds them to that message. Choose suitable modeling methods and reasonable reversible "
                    "defaults, stating material assumptions. Ask when a design choice needs the user's judgment. "
                    "Use the connected monkeyhub tools for project queries and changes: studio_request lists entry points "
                    "and studio_schema supplies their contracts on demand. Refresh evidence when it has changed or is unclear. "
                    "Preserve the exact editing base, object identity and the user's keep conditions; use existing controls "
                    "and dependencies for linked edits. Correct recoverable errors and continue; explain a concrete limit "
                    "when the available tools cannot preserve the requested design meaning. "
                    "Project files are read-only: use Studio/P036 for changes rather than editing project.json, HEAD, input, runs or records. "
                    "Show reversible candidates; do not claim approval, issuance or printer upload. "
                    f"This conversation is bound to project {session.projectId} at {session.projectDir}. "
                    + (
                        f"You are working in this Hub's own source checkout at {_source_checkout()}, which is where "
                        "you start and what you may read, change and run: its rules are in AGENTS.md, its services "
                        "start from apps/monkeyhub/run.py and apps/archflow-studio, and the design tools you call "
                        "are the ones running from it. Project data is written only through those tools and the "
                        "P036 interfaces, never by editing the project's own files. "
                        if _source_checkout() is not None else
                        "This Hub runs from an installed bundle rather than a source checkout, so there is no code "
                        "here for you to change; say so instead of describing an edit you cannot make. "
                    ) +
                    "Do not switch Hub configuration, open a different project, or guess a service URL. "
                    "If a required domain action is unavailable, say what cannot be done.\n\n"
                    + content
                )
            if running.attachments:
                prompt += ("\n\nFiles attached to this message (read-only reference material; prefer attachment_read with "
                           "attachmentId=id. Follow nextOffset for remaining content and page for PDF pages. "
                           "PDF reads extract text only; empty text does not mean the page image was inspected. "
                           "Do not use Get-Content on these paths; native Windows sandbox access may be denied. "
                           "Paths are retained for complex formats that need explicitly authorized local processing):\n")
                prompt += json.dumps([{"id": attachment.id, "name": attachment.name, "mimeType": attachment.mimeType, "path": str(path)}
                                      for attachment, path in running.attachments], ensure_ascii=False)
            prepared = None
            if running.design_context is not None:
                if running.stop.is_set():
                    return
                try:
                    # Outside the lock deliberately: preparing this reads the
                    # Hub's own session over HTTP, and that read takes the same
                    # lock this turn would still be holding.
                    prepared = _prepared_context(self.hub_url, session_id, content,
                                                 running.design_context, running.stop, deadline)
                except (HubFailure, OSError, ValueError, TimeoutError) as cause:
                    # The preparation refused, or could not be read. That is
                    # this turn's answer, in the words the refusal came with. No
                    # provider starts, and no other source is tried to get one
                    # started anyway.
                    error = (cause.error if isinstance(cause, HubFailure)
                             else HubError(code="CHAT_TOOL_FAILED", detail=_reason(cause)))
                    return
                if prepared is None:
                    # Stopped, or this turn's limit ran out while it waited. The
                    # read is abandoned either way: no provider starts on a turn
                    # that is already over, and the answer it may still produce
                    # belongs to nothing.
                    if not running.stop.is_set():
                        error = HubError(code="CHAT_TIMEOUT", detail="This turn's time limit ran out while "
                                         "its selected context was being prepared.")
                    return
                prompt += "\n\n" + _CONTEXT_NOTE + "\n" + _redact(json.dumps(prepared, ensure_ascii=False))
            if running.stop.is_set():
                return
            stage = (prepared or {}).get("confirmedStage")
            # A candidate's inherited Stage is context, not a new boundary.
            # Only the exact committed source may replace provider history.
            stage = stage if isinstance(stage, dict) and stage.get("isSource") is True else None
            automatic = (running.context_mode == "stage" and stage is not None
                         and stage["stageRef"] != session.providerStageRef)
            if running.context_mode == "project" or automatic:
                if time.monotonic() >= deadline:
                    error = HubError(code="CHAT_TIMEOUT", detail="The turn expired before a new provider session could start.")
                    return
                with self._lock:
                    retained = self._retained_continuity(self._sessions[session_id])
                session = self._fresh_provider_session(session_id, stage=stage, automatic=automatic)
                prompt += ("\n\nThis is a new provider session reconstructed from the project state above. "
                           "Previous chat messages are not included; use the current request and project evidence.")
            with self._lock:
                # A continuation's own interjections are its prompt: the Agent
                # has them as soon as it starts.
                for identifier in carried:
                    self._delivered(self._sessions[session_id], identifier)
            if session.transport == "acp":
                if running.trace:
                    running.trace.ready()
                error = self._run_acp(session_id, prompt, running)
                return
            if time.monotonic() >= deadline:
                # The CLI transport holds one total limit for the turn, so a
                # budget preparing already spent starts no process: a CLI given
                # no time would be reported as its own failure rather than as
                # the limit it actually ran into.
                error = HubError(code="CHAT_TIMEOUT", detail="This turn's time limit was spent before "
                                 "the CLI could be started.")
                return
            command, environment = self._command(session, running.attachments)
            if running.trace:
                running.trace.bind(session.nativeSessionId, session.model)
                running.trace.ready()
            if running.stop.is_set():
                return
            with self._lock:
                prompt_input = prompt = self._take_waiting(session_id, running, prompt)
                channel = running.stdin
            if channel is not None:
                blocks = [{"type": "text", "text": prompt}]
                blocks.extend({"type": "image", "source": {"type": "base64", "media_type": attachment.mimeType,
                               "data": base64.b64encode(path.read_bytes()).decode("ascii")}}
                              for attachment, path in running.attachments if attachment.mimeType in _IMAGE_MIMES)
                prompt_input = json.dumps({"type": "user", "session_id": session.nativeSessionId or session.cliStartId or session.id,
                    "parent_tool_use_id": None, "message": {"role": "user", "content": blocks}}, ensure_ascii=False) + "\n"
            kwargs = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, cwd=_source_checkout() or session.projectDir, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace", shell=False, **kwargs)
            with self._lock:
                running.process = process
            if running.stop.is_set():
                _stop_process(process)

            def feed():
                try:
                    process.stdin.write(prompt_input)
                    process.stdin.flush()
                    # Claude keeps reading: each interjection is one more
                    # stream-json user message, until the turn's result.
                    while channel is not None and (line := channel.get()) is not None:
                        process.stdin.write(line)
                        process.stdin.flush()
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

            def read_errors():
                for line in process.stderr:
                    stderr.append(_redact(line, environment)[-2000:])
                    del stderr[:-8]

            feeder = threading.Thread(target=feed, daemon=True)
            errors = threading.Thread(target=read_errors, daemon=True)
            feeder.start()
            errors.start()
            timer = threading.Timer(max(0.0, deadline - time.monotonic()),
                                    lambda: _stop_process(process) if process.poll() is None else None)
            timer.daemon = True
            timer.start()
            last_save = 0.0
            try:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self._lock:
                        # A media presentation can atomically replace the saved chat between CLI events.
                        session = self._sessions[session_id]
                        event_error, finished = self._event(session, event, environment)
                        completed_turn = completed_turn or finished
                        if event_error:
                            error = event_error
                        if finished:
                            # After its result Claude answers what it has already
                            # read and exits once stdin closes; anything sent
                            # from here on is the next prompt.
                            self._close_input(running)
                        if time.monotonic() - last_save >= 0.3:
                            self._save(session)
                            last_save = time.monotonic()
                process.wait()
            finally:
                with self._lock:
                    self._close_input(running)
                timer.cancel()
                if process.poll() is None:
                    _stop_process(process)
                process.wait(timeout=10)
                feeder.join(timeout=1)
                errors.join(timeout=1)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if not (running.stop.is_set() or running.redirected):
                if time.monotonic() >= deadline:
                    error = HubError(code="CHAT_TIMEOUT", detail="The CLI did not finish within this turn's time limit.")
                elif process.returncode and error is None:
                    detail = "".join(stderr).strip()[-1000:] or f"The CLI exited with code {process.returncode}."
                    error = HubError(code="CHAT_PROCESS_FAILED", detail=detail)
                elif not completed_turn and error is None:
                    error = HubError(code="CHAT_INCOMPLETE", detail="The CLI stopped without completing this turn.")
        except Exception as exc:
            error = HubError(code="CHAT_PROCESS_FAILED", detail=_redact(str(exc), _claude_env())[:1000] or "The installed CLI could not run.")
        finally:
            abandoned = None
            with self._lock:
                session = self._sessions[session_id]
                stopped = running.stop.is_set()
                halted = stopped or running.redirected
                self._clear_permissions(session_id)
                # What Claude read without echoing it back still reached it when
                # its turn finished normally; a stopped or failed turn read none.
                for identifier in list(running.written):
                    if not (stopped or error):
                        self._delivered(session, identifier)
                # Interjections the turn could no longer take are the next prompt,
                # unless the turn was stopped or failed or the Hub is closing.
                # A steer that left just as the turn ended was handed over already.
                handed = {row.id for row in session.messages if row.interjection == "delivered"}
                follow = [] if stopped or error or self._closing else [
                    (identifier, text) for identifier, text in running.waiting if identifier not in handed]
                following = {identifier for identifier, _ in follow}
                # Taken here, a message is never routed again by a steer that
                # finishes late; one handed over is left for that steer to settle.
                running.waiting = [row for row in running.waiting if row[0] in handed]
                for message in session.messages:
                    if message.interjection == "pending" and message.id not in following:
                        message.interjection = "undelivered"
                if not follow:
                    self._acp_tools.pop(session_id, None)
                session.status = "running" if follow else "interrupted" if stopped else "failed" if error else "idle"
                session.error = HubError(code="CHAT_STOPPED", detail="The response was stopped.") if stopped else error
                for message in session.messages:
                    if message.status == "streaming":
                        message.status = "interrupted" if halted else "failed" if error else "complete"
                session.updatedAt = _now()
                if running.trace:
                    running.trace.bind(session.acpSessionId if session.transport == "acp" else session.nativeSessionId)
                    running.trace.finish("cancelled" if halted else "failed" if error else "succeeded")
                # A replacement that never reported an identity of its own is no
                # replacement: the chat would be left with nothing to continue
                # from at all, so the continuity this turn set aside goes back. A
                # provider that did open its own session keeps it, whether or not
                # the turn it was opened for then failed. The trace above already
                # recorded what this turn itself reached, which is nothing.
                if retained is not None and session.nativeSessionId is None and session.acpSessionId is None:
                    abandoned = self._restore_continuity(session, retained)
                try:
                    self._save(session)
                finally:
                    self._running.pop(session_id, None)
                    if follow:
                        self._continue(session_id, follow, redirected=running.redirected, earlier=tuple(running.turns))
            if abandoned is not None:
                # Closing hands work to the adapter's own thread and waits for
                # it; that never happens while this store's lock is held.
                abandoned.close()

    def _close_input(self, running: _Running) -> None:
        """Close Claude's stdin once its turn has a result. The lock is held."""
        if running.stdin is not None:
            running.stdin.put(None)
            running.stdin = None

    def _continue(self, session_id: str, follow: list[tuple[str, str]], *, redirected: bool,
                  earlier: tuple[str, ...] = ()) -> None:
        """Send the interjections a finished turn could not take as the next prompt.

        The lock is held and the finished turn is already gone. The chat stays
        running; the continuation is a turn of its own with its own trace, on the
        same provider session.
        """
        session = self._sessions[session_id]
        content = "\n\n".join(text for _, text in follow)
        if redirected:
            content = ("The architect stopped your previous step to send the message below. "
                       "Everything already completed stays as it is.\n\n" + content)
        running = _Running(trace=HubTurnObserver(
            self.usage_log, follow[0][0], session.projectId, session.provider, session.model,
        ))
        self._start(session_id, running, content, carried=tuple(identifier for identifier, _ in follow),
                    earlier=earlier)

    def _tool_message(self, session: _SavedChat, item: Mapping, kind: str, environment) -> None:
        """Keep one visible row per MCP call, from started to its outcome."""
        content, candidate, failed = _tool_activity(item, environment)
        message_id = self._call_row(session, str(item.get("id") or "tool"))
        message = next((row for row in session.messages if row.id == message_id), None)
        running = kind in {"item.started", "item.updated"} and str(item.get("status") or "") not in {"completed", "failed", "cancelled", "interrupted"}
        outcome = "streaming" if running else "failed" if failed else "complete"
        if message is None:
            message = ChatMessage(id=message_id, role="tool", content=content, createdAt=_now(), status=outcome)
            session.messages.append(message)
        elif not running or message.status == "streaming":
            # A repeated started event never reopens a call that already ended.
            message.content, message.status = content, outcome
        if candidate:
            message.candidateId = candidate
        active = self._running.get(session.id)
        if active and active.trace:
            active.trace.tool(str(item.get("id") or "tool"), item.get("tool"), item.get("arguments"),
                              running=running, failed=failed, result=item.get("result"), candidate_id=candidate)
        session.updatedAt = _now()

    def _event(self, session: _SavedChat, event: dict, environment) -> tuple[HubError | None, bool]:
        kind = event.get("type")
        native = event.get("thread_id") if kind == "thread.started" else event.get("session_id")
        if isinstance(native, str):
            session.nativeSessionId = _identifier(native)
        active = self._running.get(session.id)
        trace = active.trace if active else None
        if trace and isinstance(native, str):
            trace.bind(session.nativeSessionId)
        if trace and kind == "assistant":
            trace.claude_usage(event.get("message"))
        if kind in {"error", "turn.failed"} or (kind == "result" and event.get("is_error")):
            detail = event.get("message") or event.get("error") or event.get("result") or "The provider reported a failed turn."
            if isinstance(detail, Mapping):
                # The CLI wraps its own sentence; carry that, not this process's
                # rendering of a dict, so the reader gets the provider's words.
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False, default=str)
            return HubError(code="CHAT_PROVIDER_FAILED", detail=_redact(str(detail), environment)[:1000]), False
        text, message_id, append = None, None, False
        if kind in {"item.started", "item.completed", "item.updated"}:
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "mcp_tool_call":
                # What the CLI actually did through the bound tools, kept as a
                # readable row. A failed call is this turn's news, not its end.
                self._tool_message(session, item, kind, environment)
                return None, False
            if kind != "item.started" and item.get("type") == "agent_message":
                text, message_id = item.get("text", ""), str(item.get("id", "answer"))
        elif kind == "stream_event":
            inner = event.get("event", {})
            if inner.get("type") == "content_block_delta" and inner.get("delta", {}).get("type") == "text_delta":
                text, message_id, append = inner["delta"].get("text", ""), "claude-answer", True
        elif kind == "assistant":
            blocks = event.get("message", {}).get("content", []) or []
            for row in blocks:
                # Claude opens a call here and answers it in a later user event.
                if isinstance(row, dict) and row.get("type") == "tool_use":
                    self._tool_message(session, _claude_call(row, None), "item.started", environment)
            text = "".join(row.get("text", "") for row in blocks if isinstance(row, dict) and row.get("type") == "text")
            message_id = "claude-answer"
        elif kind == "user" and event.get("isReplay") is True:
            # Claude echoes each stdin message as it reads it: an interjection
            # echoed back is one the Agent now has (#301).
            if isinstance(event.get("uuid"), str):
                self._delivered(session, event["uuid"])
            return None, False
        elif kind == "command_lifecycle":
            if event.get("state") == "started" and isinstance(event.get("command_uuid"), str):
                self._delivered(session, event["command_uuid"])
            return None, False
        elif kind == "user":
            for row in event.get("message", {}).get("content", []) or []:
                if isinstance(row, dict) and row.get("type") == "tool_result":
                    identifier = self._call_row(session, str(row.get("tool_use_id") or "tool"))
                    asked = next((item for item in session.messages if item.id == identifier), None)
                    self._tool_message(session, _claude_call(row, asked), "item.completed", environment)
            return None, False
        elif kind == "result" and isinstance(event.get("result"), str):
            text, message_id = event["result"], "claude-answer"
        if text:
            if trace:
                trace.first_response()
            # Prefix provider ids with the current user message, since CLI item
            # ids may repeat in a resumed turn.
            output_id = f"{self._answering(session)}:{message_id}"
            message = next((row for row in session.messages if row.id == output_id), None)
            if message is None:
                message = ChatMessage(id=output_id, role="assistant", content="", createdAt=_now(), status="streaming")
                session.messages.append(message)
            clean = _redact(text, environment)
            message.content = message.content + clean if append else clean
            session.updatedAt = _now()
        return None, kind in {"turn.completed", "result"}

    def stop(self, session_id: str) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            if session.sourceSessionId and session.status == "running":
                updated = session.model_copy(deep=True)
                updated.status, updated.updatedAt = "interrupted", _now()
                for message in updated.messages:
                    if message.status == "streaming":
                        message.status = "interrupted"
                self._save(updated)
                self._sessions[session_id] = updated
                return self.get(session_id)
            running = self._running.get(session_id)
            if running is None:
                return self.get(session_id)
            running.stop.set()
            process, thread = running.process, running.thread
            client = self._acp_sessions.get(session_id)
            self._clear_permissions(session_id)
        if client is not None:
            client.cancel()
        if process is not None:
            _stop_process(process)
        if thread is not None:
            thread.join(timeout=10)
        return self.get(session_id)

    def close_project(self, project_dir: str) -> None:
        """Detach only this project's agents and cancel their pending permissions."""
        target = os.path.normcase(str(Path(project_dir).resolve()))
        with self._lock:
            self._load()
            ids = [row.id for row in self._sessions.values()
                   if os.path.normcase(str(Path(row.projectDir).resolve())) == target]
        for session_id in ids:
            self.stop(session_id)
            with self._lock:
                client = self._acp_sessions.pop(session_id, None)
                self._clear_permissions(session_id)
            if client is not None:
                client.close()

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            for session_id in list(self._running):
                self._clear_permissions(session_id)
            threads = [item.thread for item in self._running.values() if item.thread]
            checking = self._check_thread
        # A connection check is read-only and short; let it finish rather than
        # leaving a CLI probe behind.
        if checking is not None:
            checking.join(timeout=10)
        # Normal Hub shutdown lets admitted work finish. The explicit stop
        # endpoint only terminates that chat's own process tree.
        for thread in threads:
            thread.join()
        for client in self._acp_sessions.values():
            client.close()
        self._acp_sessions.clear()


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


_READ = re.compile(r"^/api/(exports(?:/[A-Za-z0-9_-]+)?|project|state(?:/frame|/volumes)?|semantics|program|options|board|artifacts|model-assets/[0-9a-f]{64}/index|documents|document-annotations|studies/[A-Za-z0-9][A-Za-z0-9._-]{0,79}|decisions(?:/[A-Za-z0-9_-]+)?|drawings/(?:styles|model-view|plans/vector|plans/dimensions|corrections)|capabilities(?:/[A-Za-z0-9_.-]+)?|proposals/[A-Za-z0-9_-]+|jobs/[A-Za-z0-9_-]+|candidates/[A-Za-z0-9_-]+(?:/compare)?|admissions|working-source|working-draft/revision)$")
_POST = re.compile(r"^/api/(exports|project/modeling|intents/context|board/export|decisions(?:/[A-Za-z0-9_-]+/revisions)?|state/closure|capabilities/[A-Za-z0-9_.-]+/run|proposals|proposals/(sketch|transform|push-pull|delete|elevation)|proposals/[A-Za-z0-9_-]+/candidate|program|options|options/[A-Za-z0-9_-]+/select|candidates/combine|drawings/(elevations|sheets|section-perspectives|plans|plans/status)|admissions)$")
_WRITE = re.compile(r"^/api/(board|document-annotations|working-draft)$")
# POSTs that only read. They go to the bound Studio as a GET would, with no
# mutation admission: there is nothing to admit, recover or replay.
_POST_READS = {"/api/intents/context", "/api/drawings/plans/status"}
# Besides retained feedback, the Agent's judgments Hub binds to the user's own
# message (#294 Q3): a closed loop's admission, and a Continue on the user's words.
_BOUND_WORDS = {("POST", "/api/admissions"), ("PUT", "/api/working-draft")}
_PAGE_IMAGE_MAX_EDGE = 2048
_PAGE_IMAGE_MAX_BYTES = 4 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The bound service redirected the request.")


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
    request = Request(_url(base) + path, data=data, method=method, headers={
        "Content-Type": "application/json", **_trace_headers.get(), **(headers or {}),
    })
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
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
        raise HubFailure(exc.code, code, _redact(str(detail))[:1200]) from exc


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


def _read_drawing_page(base: str, body) -> dict:
    pages = body.get("pages") if isinstance(body, dict) else None
    if (not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict)
            or body.get("format") != "png" or body.get("zip", False) is not False):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Read one registered page with format=png and zip=false.")
    page = pages[0]
    if set(page) != {"runId", "assetSha256", "revisionRef", "pageIndex"}:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Copy runId, assetSha256, revisionRef (including null), and zero-based pageIndex from GET /api/documents or the generated drawing result.")
    max_edge = body.get("maxEdge", _PAGE_IMAGE_MAX_EDGE)
    if type(max_edge) is not int or not 1 <= max_edge <= _PAGE_IMAGE_MAX_EDGE:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "maxEdge must be an integer from 1 to 2048.")
    # The existing owner verifies the digest, exact revision and page before
    # rasterizing in memory. This POST is a read and never enters admission.
    picture = _request_json(base, "/api/board/export", "POST", {**body, "maxEdge": max_edge}, png=True)
    return {**picture, "source": {"projectId": body["projectId"], **page},
            "representation": "registered-document-page", "annotationsIncluded": False}


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


def _bound_studio(hub: str, chat_id: str | None, timeout: float = 180, *, project_id: str | None = None,
                  project_dir: str | None = None, deadline: float | None = None) -> tuple[str, dict]:
    """Resolve the chat's own Studio, then verify its process and project before use.

    With a ``deadline`` these checks share what is left of one budget instead of
    each starting ``timeout`` again; without one they keep the per-check limit
    the tool callers already have.
    """

    def left() -> float:
        return timeout if deadline is None else deadline - time.monotonic()

    if chat_id is None:
        configured_path = project_dir if project_dir is not None else _request_json(hub, "/api/settings/apps", timeout=left()).get("projectDir")
        if not configured_path:
            raise HubFailure(409, "PROJECT_REQUIRED", "Choose a project before preparing its modeling workspace.")
        actual_id, actual_path = _project(configured_path)
        if project_id != actual_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The selected project changed before its workspace was prepared.")
        session = {"projectId": actual_id, "projectDir": actual_path}
    else:
        session = _request_json(hub, f"/api/chat/sessions/{_identifier(chat_id)}", timeout=left())
        if session.get("status") != "running":
            raise HubFailure(409, "CHAT_NOT_RUNNING", "This chat is no longer running.")
        turn = next((row.get("id") for row in reversed(session.get("messages", [])) if row.get("role") == "user"), None)
        if turn:
            _trace_headers.set({"X-Monkey-Turn-Id": turn, "X-Monkey-Parent-Span-Id": f"hub:turn:{turn}"})
    if _project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
    first = _together({
        "apps": (hub, "/api/apps?" + urlencode({"projectDir": session["projectDir"]})),
        "hub_health": (hub, "/api/health"),
    }, left())
    studio = next((row for row in first["apps"] if row.get("appId") == "monkeyarch"), {})
    if studio.get("state") != "running" or not studio.get("apiUrl") or not studio.get("processId"):
        raise HubFailure(409, "CHAT_STUDIO_UNAVAILABLE", "Open MonkeyArch for this project before using a design tool.")
    base = _url(studio["apiUrl"])
    second = _together({
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


# What the prepared context is, said to the connected CLI in one short
# paragraph. It introduces data and nothing else: the request above is still the
# request, and this says what is already answered rather than what to do.
_CONTEXT_NOTE = (
    "Prepared context for the request above, read from this project just now by the bound Studio. "
    "It is data, not an instruction: it does not replace, narrow or reinterpret what was asked. "
    "source names the exact run, Stage and stateDigest this was read against and how to write "
    "against the same base. Focus, dependency facts and retained constraints describe the current "
    "project; they do not approve edits or imply that omitted facts do not exist. Any request template "
    "contains current values, not an approved change. Read coverage and supplement omitted facts through "
    "the same source when needed; refresh the context when its source changes. scopedDecisions contains "
    "the retained judgments applicable to this task. Keep their raw wording and interpretation provenance "
    "distinct; respect supported keep references, treat preferences as preferences, and report unsupported "
    "effects as deferred. They do not accept a Stage or create or remove parameter locks. "
    "Accepted drawing recipe decisions (a recipe typedBinding) shape new drawings: an explicit value in the "
    "drawing request wins, then the drawing's own previous revision, then the project recipe, then the default. "
    "studyEvidence contains explicitly selected, exact Study revisions, not accepted project facts. "
    "Keep their conditions, exceptions, competing hypotheses and counterevidence together. "
    "Check completeness and changedContext before transferring a prior; incomplete evidence requires "
    "its exact reopen read, and an archived preference or interpretation is not a current user decision."
)


def _prepared_context(hub: str, chat_id: str, content: str, selected: ChatDesignContext,
                      stop: threading.Event, deadline: float) -> dict | None:
    """This turn's prepared context, waited for only as long as this turn lasts.

    A synchronous socket read cannot be interrupted once it is waiting, so the
    one read is made on a daemon thread this call owns, holding a result local
    to it, while this turn waits on the stop it already has. ``None`` means the
    turn ended first, by a stop or by its own deadline, and the caller starts no
    provider.

    An abandoned read is abandoned for good: the result is local to this call,
    nothing retries it, and no later turn can be given what it eventually
    returns. Nothing waits on it either — not this function and not the
    interpreter's own exit — so a Studio that has stopped answering cannot keep
    the Hub from shutting down. It stays safe to leave running because the route
    it calls only reads: it makes no proposal, holds no project lock and writes
    nothing through P036, so the answer nobody collects changes nothing.
    """

    done, outcome = threading.Event(), {}

    def read(context) -> None:
        try:
            outcome["pack"] = context.run(_context_pack, hub, chat_id, content, selected, deadline)
        except BaseException as cause:  # noqa: BLE001 - re-raised below, in the turn's own thread
            outcome["cause"] = cause
        finally:
            done.set()

    threading.Thread(target=read, args=(copy_context(),), daemon=True,
                     name="hub-prepared-context").start()
    while not done.wait(0.02):
        # Ending at the stop, rather than at whatever the socket decides to do
        # next, is the whole point of waiting here instead of in the read.
        if stop.is_set() or time.monotonic() >= deadline:
            return None
    if stop.is_set() or time.monotonic() >= deadline:
        return None
    if "cause" in outcome:
        raise outcome["cause"]
    return outcome["pack"]


def _context_pack(hub: str, chat_id: str, content: str, selected: ChatDesignContext, deadline: float) -> dict:
    """This turn's selected-source facts, read before replacing provider context.

    The whole message goes to the Studio, unedited: the read context it compiles
    is the one these words actually need, and sending a shortened stand-in would
    prepare for a request nobody made. Every check belongs to the Studio and
    happens there; a refusal travels back as itself.

    ``deadline`` is when this turn's limit is spent. Every check and the read
    itself share what is left of it rather than each starting a limit of its
    own; it is not what makes the wait cancellable, which is the caller's
    business.
    """

    token = _trace_headers.set({})
    try:
        base, session = _bound_studio(hub, chat_id, deadline=deadline)
        pack = _request_json(base, "/api/intents/context", "POST", {
            "utterance": content,
            "projectId": session["projectId"],
            **selected.model_dump(exclude_none=True, exclude_defaults=True),
        }, timeout=deadline - time.monotonic())
    finally:
        # The headers belong to the turn that set them and to nothing after it.
        _trace_headers.reset(token)
    return pack


# The one action a caller may ask to see through in a single tool call, and the
# bounds it is held to. It is the capability route that already exists; nothing
# else is orchestrated, and nothing new executes anything.
_FINISHABLE = "/api/capabilities/candidate.modify_existing/run"
_CHECKPOINT = re.compile(r"^/api/proposals/[A-Za-z0-9_-]+/candidate$")
_AWAIT_MAX_S = 180
_POLL_S = 0.4


def _finish(base: str, started: Mapping, submitted: Mapping, deadline: float) -> dict:
    """Watch one accepted candidate to its end, and read back what it made.

    Every read here is a read the caller would otherwise make itself, in the
    order its own tool description states: the job, then the candidate and the
    comparison against the run the change was made from. What comes back is
    those answers, shortened — never a summary of something that was not read,
    and never a check reported as held when the run said it was unchecked.

    The action has already been accepted when this begins, so nothing that
    happens here may lose it: a read that times out, refuses or cannot reach
    the service answers with the job and the candidate that exist and the
    reads that will find them. Nothing here posts anything, ever.
    """

    job_id, candidate_id = started.get("jobId"), started.get("candidateId")
    against = submitted.get("sourceRunId")
    follow = [f"GET /api/jobs/{job_id}", f"GET /api/candidates/{candidate_id}"]
    if against:
        follow.append(f"GET /api/candidates/{candidate_id}/compare?against={against}")

    def left() -> float:
        return deadline - time.monotonic()

    def unfinished(status: str, detail: str, **extra) -> dict:
        return {**started, "status": status, "detail": detail, "next": follow, **extra}

    job = None
    while True:
        if left() <= 0:
            return unfinished(
                "running" if job is None or job.get("status") not in {"succeeded", "failed"} else job["status"],
                "this call's wait ran out; the action was accepted once and was not sent again.",
                waitedOut=True)
        try:
            job = _request_json(base, f"/api/jobs/{job_id}", timeout=left())
        except (HubFailure, OSError, TimeoutError) as cause:
            # The run is out there with a name; only this look at it failed.
            return unfinished("unknown", f"the job could not be read: {_reason(cause)}", readback="failed")
        if job.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(min(_POLL_S, max(0.0, left())))
    if job.get("status") != "succeeded":
        # A failure is the job's own words. Deciding what to do with them is the
        # caller's, and sending the request again is never this call's decision.
        return {**started, "status": "failed", "job": job, "next": follow}
    if left() <= 0:
        return {**started, "status": "succeeded", "readback": "not attempted",
                "detail": "the run finished as this call's wait ran out; read it with the paths below.",
                "next": follow}
    # The two reads that describe a finished run do not depend on each other,
    # so they are asked at once. This is the only overlap here: the job had to
    # finish before either could be asked at all.
    reads = {"candidate": (base, f"/api/candidates/{candidate_id}")}
    if against:
        reads["compare"] = (base, f"/api/candidates/{candidate_id}/compare?against={against}")
    answers = _together(reads, left(), allow_partial=True)
    errors = {name: _reason(value) for name, value in answers.items() if isinstance(value, BaseException)}
    candidate = answers["candidate"] if "candidate" not in errors else {}
    comparison = answers.get("compare") if "compare" not in errors else None
    result = {
        **started,
        "status": "succeeded",
        "readback": "failed" if errors else "ok",
        "candidate": {
            key: candidate.get(key) for key in
            ("candidateId", "stateDigest", "changedVsProjection", "seatExecutionComplete",
             "relationChecks", "harness", "honesty")
            if key in candidate
        },
        # What the run saved, by the fields that say whether it is really there.
        "artifacts": [
            {key: row.get(key) for key in
             ("runId", "modelSource", "sourceStageRef", "fileName", "relativePath", "representation", "lengthUnit",
              "objectCount", "readbackVerified", "available", "unavailableReason", "sha256")
             if key in row}
            for row in candidate.get("artifacts", ())
        ],
        "objects": candidate.get("objects"),
        "objectReadbackError": candidate.get("objectReadbackError"),
        # The comparison's own objects, not a count of them: which object
        # changed, from which box to which box, and which ones did not move.
        # Whether that satisfies what was kept is the reader's judgement, made
        # on these facts rather than on a verdict invented here.
        "compare": {
            "against": against,
            **{key: comparison.get(key) for key in
               ("changed", "unchanged", "added", "removed", "tolerance", "why", "honesty")
               if key in comparison},
            "objects": [
                {key: row.get(key) for key in
                 ("name", "componentId", "producerOp", "status", "before", "after") if key in row}
                for row in comparison.get("objects", ())
            ],
        } if comparison is not None else None,
        # Nothing is left to do: the job finished and both readings of it are
        # above. Listing the reads that produced them would invite a second
        # round of the calls this one already made.
        "next": [],
    }
    if errors:
        # A missing comparison must not discard objects already read, nor may
        # a successful comparison stand in for a missing candidate. Keep each
        # answer once and direct recovery only to the reads still missing.
        result.update(
            detail="The run finished. Completed reads: "
                   + (", ".join(name for name in reads if name not in errors) or "none")
                   + ". Completed responses are included below and do not need another read. "
                   "Only the reads in next are missing; overall verification remains incomplete. "
                   + "; ".join(f"{name}: {reason}" for name, reason in errors.items()),
            readbackErrors=errors,
            next=[f"GET {reads[name][1]}" for name in errors],
        )
        if "candidate" in errors:
            for key in ("candidate", "artifacts", "objects", "objectReadbackError"):
                result.pop(key)
        if "compare" in errors:
            result.pop("compare")
    return result


def _reason(cause: BaseException) -> str:
    """One sentence about a failed read, in the words it came with."""

    if isinstance(cause, HubFailure):
        return _redact(str(cause.error.detail))[:400]
    return _redact(f"{type(cause).__name__}: {cause}")[:400]


def call_tool(hub: str, chat_id: str, name: str, arguments: dict):
    token = _trace_headers.set({})
    try:
        return _call_tool(hub, chat_id, name, arguments)
    finally:
        _trace_headers.reset(token)


def _binds_words(method: str, path: str) -> bool:
    """Whether this request is a judgment Hub binds to the user's own message."""
    return (method == "POST" and path.startswith("/api/decisions")) or (method, path) in _BOUND_WORDS


def _user_message(chat_id: str, session: dict, purpose: str) -> dict:
    """The user message the Agent is acting on: the last one it has been given.

    An interjection still waiting for the Agent's next step (#301) is nothing it
    could have acted on yet, so it binds no judgment until it is delivered.
    """
    message = next((row for row in reversed(session.get("messages", [])) if row.get("role") == "user"
                    and row.get("interjection") in (None, "delivered", "restarted")), None)
    if session.get("id") != chat_id or not message or not message.get("id"):
        raise HubFailure(409, "CHAT_FEEDBACK_SOURCE", f"{purpose} needs this conversation's current user message.")
    return message


def _user_words(message: dict, quote: str | None, purpose: str) -> str:
    """The user's own words in that message: all of it, or one exact passage it holds once.

    Hub extracts them itself; a provider only points at them, so it can neither
    invent nor rewrite what the user said.
    """
    wording = message.get("content") or ""
    if not wording.strip():
        raise HubFailure(409, "CHAT_FEEDBACK_SOURCE", f"{purpose} needs the user's own words, and the current user message has none.")
    if quote is not None:
        if not isinstance(quote, str) or not quote.strip():
            raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote must select a nonempty exact passage from this user message.")
        start = wording.find(quote)
        if start < 0 or wording.find(quote, start + 1) >= 0:
            raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote must occur exactly once, unchanged, in this user message. Include enough surrounding words to identify it.")
        wording = wording[start:start + len(quote)]
    if len(wording) > 2000:
        raise HubFailure(422, "CHAT_FEEDBACK_TOO_LONG", "Select the exact passage of the user's words with feedbackQuote beside method/path/body (at most 2000 characters); the user need not repeat the message.")
    return wording


def _admission_body(chat_id: str, session: dict, body: dict, quote: str | None = None) -> dict:
    """Close one chat task's loop in the Agent's name, bound to the message it answers.

    The task is always ``hub-chat``. The user's words are bound only where they
    carry the decision: a selected passage, or the message itself for a
    rejection, since the Agent rejects only on what the user said (Q3).
    """
    if {"messageSource", "message_source", "rawLanguage", "raw_language"}.intersection(body):
        raise HubFailure(422, "CHAT_ADMISSION_INVALID", "The chat fills messageSource and rawLanguage from this user turn; never supply them.")
    task = body.get("task", {"kind": "hub-chat"})
    if not isinstance(task, dict) or task.get("kind", "hub-chat") != "hub-chat":
        raise HubFailure(422, "CHAT_ADMISSION_INVALID", "A chat closes its own task as kind hub-chat; the ui and retroactive kinds are a person's act.")
    message = _user_message(chat_id, session, "An admission")
    rejects = any(isinstance(row, dict) and row.get("outcome") == "rejected" for row in body.get("results") or ())
    bound = {**body, "projectId": session["projectId"], "task": {**task, "kind": "hub-chat"},
             "messageSource": {"sessionId": chat_id, "messageId": message["id"]}}
    if quote is not None or rejects:
        bound["rawLanguage"] = _user_words(message, quote, "A rejection" if rejects else "An admission")
    return bound


def _continue_body(chat_id: str, session: dict, body: dict, quote: str | None = None) -> dict:
    """Move the Working Head only on the user's own words, bound to their message (Q3).

    The Runtime still owns the exact-run check, the position's CAS and the
    retained event; this adapter refuses a Continue no user message asks for.
    """
    if set(body) - {"projectId", "runId", "baseRevisionSha256", "branchId"}:
        raise HubFailure(422, "CHAT_CONTINUE_INVALID", "A Continue from chat takes runId, baseRevisionSha256 and optional branchId; the chat binds the user's message and words.")
    if not isinstance(body.get("runId"), str) or not body["runId"]:
        raise HubFailure(422, "CHAT_CONTINUE_INVALID", "Name the result to continue on; returning to the default is the architect's own action.")
    message = _user_message(chat_id, session, "A Continue")
    return {**body, "projectId": session["projectId"], "rawLanguage": _user_words(message, quote, "A Continue"),
            "messageSource": {"sessionId": chat_id, "messageId": message["id"]}}


def _feedback_body(hub: str, base: str, chat_id: str, session: dict, path: str, body: dict,
                   quote: str | None = None) -> dict:
    """Bind ordinary feedback to real user words, not a model's claimed authorship.

    The Runtime still owns the decision contract, source validation, CAS and
    authorization. This adapter only narrows what a chat can ask it to write.
    """
    message = _user_message(chat_id, session, "Feedback")
    wording = _user_words(message, quote, "Feedback")
    provenance = {"sessionId": chat_id, "messageId": message["id"]}
    if path == "/api/decisions":
        reserved = {"rawLanguage", "raw_language", "messageSource", "message_source", "sourceKind", "source_kind"}
        if reserved.intersection(body) or body.get("disposition") not in {"avoid", "keep"}:
            raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Chat can save only avoid/keep feedback. Its words, message source and agent attribution are filled from this user turn.")
        return {**body, "projectId": session["projectId"], "rawLanguage": wording,
                "messageSource": provenance, "sourceKind": "agent"}
    if set(body) - {"projectId", "expectedRevisionRef", "action"} or body.get("action") != "revoke":
        raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Chat can only revoke its retained avoid/keep feedback; the reason and message source come from this user turn.")
    history = _request_json(base, path.removesuffix("/revisions"))
    revisions = history.get("revisions", [])
    latest = revisions[-1] if revisions else {}
    origin = latest.get("messageSource") or {}
    if (history.get("projectId") != session["projectId"] or latest.get("sourceKind") != "agent"
            or latest.get("disposition") not in {"avoid", "keep"} or not origin.get("sessionId") or not origin.get("messageId")):
        raise HubFailure(403, "CHAT_FEEDBACK_UNAVAILABLE", "This record is not ordinary feedback saved from a user chat message.")
    original = _request_json(hub, f"/api/chat/sessions/{_identifier(origin['sessionId'])}")
    original_message = next((row for row in original.get("messages", [])
                             if row.get("id") == origin["messageId"] and row.get("role") == "user"), None)
    original_text = (original_message or {}).get("content", "")
    original_words = latest.get("rawLanguage", "")
    start = original_text.find(original_words)
    if (original.get("id") != origin["sessionId"] or original.get("projectId") != session["projectId"]
            or original.get("projectDir") != session["projectDir"] or not original_message
            or not original_words or start < 0 or original_text.find(original_words, start + 1) >= 0):
        raise HubFailure(403, "CHAT_FEEDBACK_SOURCE", "The original feedback message does not match this project's retained judgment.")
    return {**body, "projectId": session["projectId"], "reason": wording,
            "revisionMessageSource": provenance}


# The Agent's one bounded look (#303). The runtime route keeps no loop state:
# its caller holds the allowance and sends it back with every review. For the
# Agent that caller is Hub, with one allowance for each user message the Agent
# answers, kept in this adapter while that is the message it answers. The
# stdio loop answers one call at a time, so nothing else touches it meanwhile.
_VISUAL_REVIEW_FIELDS = {"domain", "sourceRefs", "viewRecipe", "task", "criteria", "preserve",
                         "priorObservations", "knownFacts", "reason", "addressedFindingIds"}
# The runtime's allowance for each class the Agent can declare. The route
# refuses any other number, so these can only ever agree with it.
_VISUAL_ALLOWED = {"deterministic_edit": 0, "spatial_formal": 2}
_POLISH_ROUNDS = range(1, 5)
# Polish beyond the two looks a spatial task gets needs the user's own words
# asking to keep refining; the Agent's declaration alone never buys more.
_KEEP_REFINING = re.compile(
    r"继续优化|再优化|打磨|精修|反复推敲|keep (?:refining|polishing|improving|iterating)"
    r"|refine (?:it |this |them )?further|further refinement|\bpolish", re.IGNORECASE)
# Up to four owner-rendered views and one provider look, which is bounded itself.
_VISUAL_REVIEW_WAIT_S = 300
_visual_allowances: dict[str, tuple[str, dict]] = {}


def _allowance_note(held: dict) -> str:
    return f"This message's {held['taskClass']} allowance: {held['used']} of {held['allowed']} reviews used."


def _visual_allowance(chat_id: str, message: dict, declared, rounds) -> dict:
    """The allowance of the user message the Agent answers, for the class it declares.

    The Agent declares the class and Hub records it. Until a review is spent a
    new declaration replaces it; from then on the class stays with the
    message, so declaring again never buys another look.
    """
    if declared == "polish":
        if type(rounds) is not int or rounds not in _POLISH_ROUNDS:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "A polish task names polishRounds from 1 to 4.")
        if rounds > 2 and not _KEEP_REFINING.search(message.get("content") or ""):
            raise HubFailure(409, "VISUAL_POLISH_NOT_ASKED",
                             "More than two polish rounds need the user's own words in this message asking to keep "
                             "refining (继续优化, 打磨, keep refining). Declare polishRounds 1-2, or spatial_formal.")
        allowed = rounds
    elif declared in _VISUAL_ALLOWED:
        if rounds is not None:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "polishRounds belongs to a polish task.")
        allowed = _VISUAL_ALLOWED[declared]
    else:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "taskClass is spatial_formal, polish or deterministic_edit.")
    turn, held = _visual_allowances.get(chat_id, (None, None))
    if turn == message["id"] and held["used"]:
        if (held["taskClass"], held["allowed"]) != (declared, allowed):
            raise HubFailure(409, "VISUAL_TASK_CLASS_FIXED",
                             f"A review of this message was spent as {held['taskClass']}, which it keeps until the "
                             f"user's next message. {_allowance_note(held)}")
        return held
    held = {"taskClass": declared, "allowed": allowed, "used": 0, "lastFindingIds": []}
    _visual_allowances[chat_id] = (message["id"], held)
    return held


def _visual_review(hub: str, chat_id: str, arguments: dict) -> dict:
    """One bounded look through the bound Studio, under the answered message's allowance.

    The runtime renders every frame from the exact sources named, answers
    findings rather than images and writes nothing. Hub supplies the project
    and the allowance and keeps what the answer says of it: a refusal spends
    nothing, and a call the provider may have answered is spent. A finding
    that touches a preserve condition is marked escalate: it is a question for
    the architect, which another review cannot settle.
    """
    if not isinstance(arguments, dict) or set(arguments) - _VISUAL_REVIEW_FIELDS - {"taskClass", "polishRounds"}:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "visual_review takes taskClass, polishRounds for a polish task "
                         "and the review's own fields; Hub fills projectId and budgetState.")
    base, session = _bound_studio(hub, chat_id)
    message = _user_message(chat_id, session, "A visual review")
    held = _visual_allowance(chat_id, message, arguments.get("taskClass"), arguments.get("polishRounds"))
    body = {key: value for key, value in arguments.items() if key in _VISUAL_REVIEW_FIELDS}
    body.update(projectId=session["projectId"], budgetState=dict(held))

    def spent(detail: str) -> str:
        held.update(used=held["used"] + 1, lastFindingIds=[])
        return f"{detail} {_allowance_note(held)}"

    try:
        answer = _request_json(base, "/api/visual-reviews", "POST", body, timeout=_VISUAL_REVIEW_WAIT_S)
    except HubFailure as refused:
        # The route refuses before its provider call with a 4xx; a 5xx means
        # the call was made, or may have been.
        detail = refused.error.detail
        detail = spent(detail) if refused.status >= 500 else f"{detail} {_allowance_note(held)}"
        raise HubFailure(refused.status, refused.error.code, detail) from refused
    except URLError:
        raise  # It never reached the Studio: nothing was spent.
    except (OSError, ValueError) as lost:
        raise HubFailure(504, "VISUAL_REVIEW_UNANSWERED", spent(
            f"The Studio did not answer this review ({_reason(lost)}), so it counts as spent.")) from lost
    observation = answer.get("observation") if isinstance(answer, dict) else None
    state = answer.get("budgetState") if isinstance(answer, dict) else None
    if (not isinstance(observation, dict) or not isinstance(state, dict) or type(state.get("used")) is not int
            or (state.get("taskClass"), state.get("allowed")) != (held["taskClass"], held["allowed"])):
        raise HubFailure(502, "CHAT_TOOL_FAILED", spent("The Studio answered this review outside its contract."))
    held.update(used=state["used"], lastFindingIds=list(state.get("lastFindingIds") or ()))
    findings = [{**row, "escalate": any(str(ref).startswith("preserve:") for ref in row.get("targetRefs") or ())}
                for row in observation.get("observations") or () if isinstance(row, dict)]
    return {"observation": {**observation, "observations": findings}, "usage": answer.get("usage"),
            "allowance": {key: held[key] for key in ("taskClass", "allowed", "used")}}


def _call_tool(hub: str, chat_id: str, name: str, arguments: dict):
    from . import computer_tools

    if name == "chat_present":
        return _present_tool(hub, chat_id, arguments, os.environ.get("MONKEYHUB_PRESENTATION_TOKEN", ""))
    if name == "attachment_read":
        if not isinstance(arguments, dict) or set(arguments) - {"attachmentId", "offset", "limit", "page"}:
            raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "Use attachmentId and optional offset, limit, and page only.")
        attachment_id = arguments.get("attachmentId")
        if not isinstance(attachment_id, str):
            raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "An attachmentId from this conversation is required.")
        attachment_id = _identifier(attachment_id)
        offset, limit, page = arguments.get("offset", 0), arguments.get("limit", 32768), arguments.get("page", 1)
        _attachment_read_paging(offset, limit, page)
        session = _request_json(hub, f"/api/chat/sessions/{_identifier(chat_id)}")
        if session.get("status") != "running":
            raise HubFailure(409, "CHAT_NOT_RUNNING", "This chat is no longer running.")
        if _project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
        return _request_json(hub, f"/api/chat/sessions/{chat_id}/attachments/{attachment_id}/read?"
                             + urlencode({"offset": offset, "limit": limit, "page": page}))
    if name in computer_tools.ROUTES:
        # Nothing is checked twice: the policy gate, the allow-list and every
        # refusal code belong to the route, so the CLI reads what an HTTP
        # caller reads. This conversation's project is not involved.
        return computer_tools.call(hub, name, arguments)
    if name == "visual_review":
        # A tool of its own rather than a studio_request path: Hub holds the
        # allowance, so the route is on no request allow-list to go around it.
        return _visual_review(hub, chat_id, arguments)
    method, path = str(arguments.get("method", "GET")).upper(), arguments.get("path", "")
    parsed = urlsplit(path)
    allowed = {"GET": _READ, "POST": _POST, "PUT": _WRITE}
    if "feedbackQuote" in arguments and (name != "studio_request" or not _binds_words(method, parsed.path)
                                         or not isinstance(arguments["feedbackQuote"], str)):
        raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote only selects the user's words for feedback, an admission or a Continue.")
    if "producer" in arguments and name != "studio_schema":
        raise HubFailure(422, "CHAT_TOOL_INVALID", "producer selects an authoring schema; it belongs to studio_schema.")
    if "operationId" in arguments and (name != "studio_request" or method == "GET" or (
            method == "POST" and parsed.path in {"/api/board/export", "/api/drawings/plans/status"})):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "operationId identifies a Studio mutation request.")
    if "awaitSeconds" in arguments and name != "studio_request":
        # Only one tool can wait for anything. Quietly dropping the option here
        # would answer at once and look like the wait had happened.
        raise HubFailure(422, "CHAT_TOOL_INVALID",
                         f"{name} has nothing to wait for; awaitSeconds is studio_request's option for "
                         f"POST {_FINISHABLE}.")
    if name == "fab_request":
        session = _request_json(hub, f"/api/chat/sessions/{_identifier(chat_id)}")
        if session.get("status") != "running":
            raise HubFailure(409, "CHAT_NOT_RUNNING", "This chat is no longer running.")
        if _project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
        if method == "GET" and path == "/api/fab/profiles":
            return _request_json(hub, path)
        if method == "POST" and path == "/api/fab/send":
            body = dict(arguments.get("body") or {})
            body["dryRun"] = True
            body.pop("accessCode", None)
            return _request_json(hub, path, "POST", body)
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "The chat can list Fab profiles and validate a prepared job; uploads remain explicit in MonkeyFab.")
    base, session = _bound_studio(hub, chat_id)
    # Asking what a documented action takes is not calling it. The path is
    # checked against the same allow-list either way, with a schema question's
    # `{id}` segments standing for the id they name, so the templates this
    # tool's own description lists can actually be read.
    checked = re.sub(r"\{[^}/]+\}", "id", parsed.path) if name == "studio_schema" else parsed.path
    if parsed.scheme or parsed.netloc or parsed.fragment or method not in allowed or not allowed[method].fullmatch(checked):
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "This action is not exposed to the chat.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(query[key] != [session["projectId"]] for key in ("projectId", "project_id") if key in query):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "A tool cannot select another project.")
    if name == "studio_schema":
        document = _request_json(base, "/openapi.json")
        template = next((route for route in document["paths"] if re.fullmatch(re.sub(r"\{[^}]+\}", r"[^/]+", route), parsed.path)), None)
        operation = document["paths"].get(template, {}).get(method.lower())
        if operation is None:
            raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "The running Studio has no matching action.")
        if method == "POST" and parsed.path == "/api/exports":
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            schema["properties"].pop("upload", None)
            schema["properties"]["attachmentId"] = {
                "type": "string", "format": "uuid",
                "description": "Exact attachment ID from this conversation. Choose this OR projectRevision OR sourceArtifactId; Hub transfers the bytes.",
            }
        if method == "POST" and parsed.path.startswith("/api/decisions"):
            # Expose the Runtime's real schema, narrowed to the chat capability;
            # provenance is supplied by this adapter, never by the provider.
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            creating = parsed.path == "/api/decisions"
            hidden = {"rawLanguage", "messageSource", "sourceKind"} if creating else {"reason", "revisionMessageSource", "replacement"}
            schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in hidden}
            schema["required"] = [key for key in schema.get("required", []) if key not in hidden]
            schema["properties"]["disposition" if creating else "action"]["enum"] = ["avoid", "keep"] if creating else ["revoke"]
        if (method, parsed.path) in _BOUND_WORDS:
            # Hub binds the user's message and words; the provider supplies neither.
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            hidden = {"messageSource", "rawLanguage"}
            schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in hidden}
            schema["required"] = [key for key in schema.get("required", []) if key not in hidden]
            if method == "POST":
                task = document["components"]["schemas"]["AdmissionTaskDto"]["properties"]
                task["kind"] = {**task["kind"], "enum": ["hub-chat"]}
        producer = arguments.get("producer")
        if producer is not None:
            if method != "POST" or parsed.path != "/api/proposals" or not isinstance(producer, str):
                raise HubFailure(422, "CHAT_TOOL_INVALID", "producer selects the input schema of POST /api/proposals.")
            entity = document.get("components", {}).get("schemas", {}).get("SemanticEditRequestDto", {}).get("properties", {}).get("entities", {}).get("items", {})
            available = set()
            matched = False
            for variant in entity.get("anyOf", []):
                fields = variant.get("properties", {}).get("fields", {})
                alternatives = fields.get("anyOf")
                if alternatives is None:
                    continue
                for item in alternatives:
                    available.update(item.get("properties", {}).get("producer", {}).get("enum", []))
                selected = [item for item in alternatives
                            if producer in item.get("properties", {}).get("producer", {}).get("enum", [])]
                if selected:
                    matched = True
                    fields["anyOf"] = selected
            if not matched:
                raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", f"No authoring schema for {producer!r}; available producers: {sorted(available)}")
            # This query asks for one author's inputs. The response, including
            # the same edit payload again, is available through the ordinary
            # unfiltered schema query and need not accompany this request.
            operation = {key: value for key, value in operation.items() if key != "responses"}
        schemas, pending = {}, [operation]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                reference = value.get("$ref", "")
                key = reference.rsplit("/", 1)[-1]
                if reference.startswith("#/components/schemas/") and key not in schemas:
                    schemas[key] = document.get("components", {}).get("schemas", {}).get(key, {})
                    pending.append(schemas[key])
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return {"path": template, "method": method, "operation": operation, "components": {"schemas": schemas},
                **({"producer": producer, "scope": "request inputs for this producer"} if producer is not None else {})}
    if name != "studio_request":
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "Unknown chat tool.")
    wait = arguments.get("awaitSeconds")
    checkpoint = method == "POST" and _CHECKPOINT.fullmatch(parsed.path) is not None
    if wait is not None:
        if not isinstance(wait, int) or isinstance(wait, bool) or not 1 <= wait <= _AWAIT_MAX_S:
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds is a whole number of seconds from 1 to {_AWAIT_MAX_S}.")
        if not (method == "POST" and (parsed.path == _FINISHABLE or checkpoint)):
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds supports POST {_FINISHABLE} or the final POST /api/proposals/{{id}}/candidate. It is not a field of the "
                             "request body, and every other action answers as it is, without waiting.")
        if not checkpoint and (not isinstance(arguments.get("body"), dict) or not arguments["body"].get("sourceRunId")):
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             "waiting for this action means answering with the comparison against the run "
                             "it was made from, so the body must name sourceRunId — the run the description "
                             "was read against. Without it, send the request without awaitSeconds and read "
                             "the job and the candidate yourself.")
    body = arguments.get("body")
    if body is not None:
        if not isinstance(body, dict):
            raise HubFailure(422, "CHAT_TOOL_INVALID", "The request body must be an object.")
        body = dict(body)
        if body.get("projectId", session["projectId"]) != session["projectId"]:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "A tool cannot select another project.")
        if parsed.path in {"/api/proposals", "/api/board/export"}:
            body["projectId"] = session["projectId"]
        if method == "POST" and parsed.path == "/api/drawings/plans":
            # A cut plan the Agent asks for is its reading of what the user
            # said, so its revision says so (05 §5). Only a person's own
            # request is human, and a correction suggestion counts only those,
            # so the Agent cannot claim it: the chat fills the kind, as it
            # does for feedback decisions.
            if {body.pop(key, None) for key in ("sourceKind", "source_kind")} - {None, "agent"}:
                raise HubFailure(422, "CHAT_TOOL_INVALID", "The chat marks your cut-plan requests sourceKind=agent; "
                                 "only a person's own request in Drawings is human. Send it without sourceKind.")
            body["sourceKind"] = "agent"
    if method == "POST" and parsed.path == "/api/exports" and isinstance(body, dict):
        attachment_id = body.pop("attachmentId", None)
        if attachment_id is not None:
            if any(body.get(key) is not None for key in ("upload", "projectRevision", "sourceArtifactId")):
                raise HubFailure(422, "EXPORT_SOURCE_AMBIGUOUS", "Choose the project revision or one attachment, not both.")
            body["upload"] = _request_json(hub, f"/api/chat/sessions/{_identifier(chat_id)}/attachments/{_identifier(attachment_id)}/model-source")
    comparison = body or {}
    if method == "POST" and parsed.path.startswith("/api/decisions"):
        if parsed.query or not isinstance(body, dict):
            raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Feedback takes its scope and exact source in the body, without query parameters.")
        body = _feedback_body(hub, base, chat_id, session, parsed.path, body, arguments.get("feedbackQuote"))
    if (method, parsed.path) in _BOUND_WORDS:
        if parsed.query or not isinstance(body, dict):
            raise HubFailure(422, "CHAT_TOOL_INVALID", f"{method} {parsed.path} takes its whole request in the body, without query parameters.")
        bind = _admission_body if method == "POST" else _continue_body
        body = bind(chat_id, session, body, arguments.get("feedbackQuote"))
    if method == "POST" and parsed.path == "/api/board/export":
        if parsed.query:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "The registered page read takes its source in the body, without query parameters.")
        return _read_drawing_page(base, body)
    if wait is not None and checkpoint:
        proposal = _request_json(base, parsed.path.removesuffix("/candidate"))
        comparison = {"sourceRunId": proposal.get("sourceRunId")}
    if method in {"POST", "PUT"} and parsed.path not in _POST_READS:
        from uuid import uuid5, NAMESPACE_URL
        runtime_id = str(uuid5(NAMESPACE_URL, f"{session['projectId']}:{os.path.normcase(str(Path(session['projectDir']).resolve()))}"))
        operation_id = arguments.get("operationId") or str(uuid4())
        try:
            operation_id = str(UUID(operation_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise HubFailure(422, "OPERATION_ID_INVALID", "operationId must be a UUID.") from exc
        started = _request_json(hub, f"/api/runtime/projects/{runtime_id}/studio{path}", method, body,
                                headers={"Idempotency-Key": operation_id, "X-Monkey-Chat": chat_id})
    else:
        started = _request_json(base, path, method, body)
    if isinstance(started, dict) and method == "GET" and parsed.path.startswith("/api/exports/"):
        if started.get("status") == "succeeded" and started.get("downloadPath") == parsed.path + "/bytes":
            started = started | {"downloadUrl": base + started["downloadPath"]}
    if wait is None:
        if method == "POST" and parsed.path in {
            "/api/proposals", "/api/proposals/sketch", "/api/proposals/transform",
            "/api/proposals/push-pull", "/api/proposals/delete", "/api/proposals/elevation",
        } and isinstance(started, dict) and "proposalId" in started:
            # The client just authored these edits. Echoing both the edits and
            # their full operator makes each model continuation read them twice.
            # Keep the checked change, source, impact and conflicts; the ordinary
            # GET still exposes the complete proposal when inspection needs it.
            started = {key: value for key, value in started.items() if key != "decisionOperator"}
            if isinstance(started.get("change"), dict):
                started["change"] = {key: value for key, value in started["change"].items() if key != "edits"}
            # A parameterized form can have thousands of coordinate changes.
            # Bound only these repeated lists; keep every conflict, lock, keep
            # condition and coverage limitation in the immediate response.
            for section, field in (("change", "changes"), ("impact", "direct")):
                values = started.get(section, {}).get(field)
                if isinstance(values, list) and len(values) > 100:
                    started[section] = {**started[section], field: values[:100],
                                        f"{field}Count": len(values), f"{field}Omitted": len(values) - 100}
                    started["detailsPath"] = f"/api/proposals/{started['proposalId']}"
        if method == "PUT" and parsed.path == "/api/working-draft" and isinstance(started, dict):
            # The position also lists every recovery row and any local recovery
            # draft. The Continue needs only where the head now is and the
            # revision a later Continue compares against.
            started = {key: started.get(key) for key in ("projectId", "revisionSha256", "current")}
        return started
    # One POST has happened. From here on this call only reads.
    return _finish(base, started, comparison, time.monotonic() + wait)


_PRESENTATION_INSTRUCTIONS = (
    "This connection displays results in the bound MonkeyHub conversation. Call presentation_bind once if available. "
    "For each external user request, call chat_present with kind=user and fresh UUID turnId/messageId; "
    "then default to publishing public progress, answers and selected images here without asking the user again. "
    "Use kind=progress only for public commentary, never hidden reasoning. Use kind=assistant for text and media. "
    "A streaming snapshot uses the same messageId and a strictly increasing revision, with full content and retained attachments. "
    "Use status=streaming while work continues; finish with complete, failed or interrupted. "
    "Keep the source host response concise with the Hub URL. This does not suppress mandatory host output. "
    "No call here starts another model. On disconnect or refusal, report it in the source host; reconnect with presentation_bind, "
    "then replay only the same presentation snapshot, never a design mutation. "
    "For Hub-native conversations, normal answers already stream automatically; use chat_present for media with status=streaming. "
    "Project drawings belong to existing project document APIs; reference runId/assetSha256/revisionRef/pageIndex exactly. "
    "A display reference does not mean a Board write succeeded."
)


def _present_tool(hub: str, chat_id: str, arguments: dict, token: str):
    session = _request_json(hub, f"/api/chat/sessions/{_identifier(chat_id)}")
    body = dict(arguments)
    if set(body) - {"turnId", "messageId", "revision", "kind", "content", "status", "attachments", "documents"}:
        raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Use only the documented presentation fields; this connection fixes its destination.")
    if not session.get("sourceSessionId") and "turnId" not in body:
        user = next((row for row in reversed(session.get("messages", [])) if row["role"] == "user"), None)
        if user:
            body["turnId"] = user["id"]
    uploads = []
    for item in body.get("attachments", []):
        item = dict(item)
        if "path" in item:
            if set(item) - {"path", "name", "mimeType"}:
                raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "A local file takes path and optional name/mimeType.")
            path = Path(item.pop("path")).resolve(strict=True)
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Select one file no larger than 20 MiB.")
            item.setdefault("name", path.name)
            item.setdefault("mimeType", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            item["data"] = base64.b64encode(path.read_bytes()).decode("ascii")
        uploads.append(item)
    body.update(projectId=session["projectId"], sourceSessionId=session.get("sourceSessionId") or f"hub:{chat_id}", attachments=uploads)
    request = ChatPresentationRequest.model_validate(body)
    result = _request_json(hub, f"/api/chat/sessions/{chat_id}/presentation", "POST", request.model_dump(),
                           headers={"Authorization": "Bearer " + token})
    return {"chatId": chat_id, "messageId": request.messageId, "revision": request.revision,
            "status": result["status"], "url": hub + "/?" + urlencode({"chatId": chat_id})}


def _mcp(hub: str, chat_id: str | None, external: ChatPresentationBindRequest | None = None) -> None:
    from . import computer_tools

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    presentation_binding = None
    request_fields = {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT"]},
        "path": {"type": "string"},
        "body": {"type": "object", "additionalProperties": True},
    }
    # Two schemas, because the option belongs to one tool. A tool that cannot
    # wait must not advertise waiting: an ignored argument would read as a wait
    # that happened.
    input_schema = {"type": "object", "properties": dict(request_fields),
                    "required": ["method", "path"], "additionalProperties": False}
    schema_input = {**input_schema, "properties": {
        **input_schema["properties"],
        "producer": {"type": "string", "description": "For POST /api/proposals semantic authoring, select prism, loft, wall or planar-surface to read only that producer's request contract, excluding unrelated geometry and response schemas."},
    }}
    request_schema = {
        "type": "object", "properties": {
            **request_fields,
            "operationId": {"type": "string", "format": "uuid", "description": "Optional stable identity for this mutation. Reusing it returns the same admission/result and never executes the request twice. Different requests must use different ids."},
            "feedbackQuote": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "Only for POST /api/decisions, /api/decisions/{id}/revisions, /api/admissions or PUT /api/working-draft: select one exact, unique, continuous passage in the current user's message that carries the decision. Hub extracts these unedited words itself and retains the original message identity. Use for long messages; invented, rewritten or ambiguous passages are refused. Omit to retain the entire message when it fits."},
            "awaitSeconds": {
                "type": "integer", "minimum": 1, "maximum": _AWAIT_MAX_S,
                "description": "Wait for one submitted change, in seconds; 60 suits an ordinary change. "
                               "Place beside method/path/body. Supported by POST /api/proposals/{id}/candidate "
                               "(no body; source comes from the proposal), or POST " + _FINISHABLE +
                               " (body requires sourceRunId). Returns job, candidate and source comparison when available. "
                               "On timeout, continue the returned reads; do not resubmit the mutation. Other paths do not support waiting.",
            },
        }, "required": ["method", "path"], "additionalProperties": False,
    }
    # The review's own fields as the runtime route names them. projectId and
    # budgetState are Hub's to fill, so they are not offered at all.
    review_schema = {"type": "object", "properties": {
        "taskClass": {"type": "string", "enum": ["spatial_formal", "polish", "deterministic_edit"]},
        "polishRounds": {"type": "integer", "minimum": 1, "maximum": 4,
                         "description": "Only for polish: its rounds; above 2 only when the user asked to keep refining."},
        "reason": {"type": "string", "enum": ["first_bundle", "after_repair", "polish_round"]},
        "domain": {"type": "string", "enum": ["modeling", "board", "drawing", "render"]},
        "sourceRefs": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
            "type": "object", "properties": {
                "kind": {"type": "string", "enum": ["model", "page"]}, "runId": {"type": "string"},
                "stateDigest": {"type": "string", "description": "model only"},
                "assetSha256": {"type": "string"},
                "revisionRef": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "page only; null when the registration has none"},
                "pageIndex": {"type": "integer", "minimum": 0, "description": "page only"},
            }, "required": ["kind", "runId", "assetSha256"], "additionalProperties": False}},
        "viewRecipe": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string"}},
        "task": {"type": "string", "minLength": 1, "maxLength": 600},
        "criteria": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "properties": {"criterionId": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,39}$"},
                                             "text": {"type": "string", "minLength": 1, "maxLength": 300}},
            "required": ["criterionId", "text"], "additionalProperties": False}},
        "preserve": {"type": "array", "maxItems": 6, "items": {"type": "string", "minLength": 1, "maxLength": 300}},
        "knownFacts": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 120}},
        "priorObservations": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "properties": {"findingRef": {"type": "string"}, "type": {"type": "string"},
                                             "description": {"type": "string"}},
            "required": ["findingRef", "type", "description"], "additionalProperties": False}},
        "addressedFindingIds": {"type": "array", "maxItems": 8, "items": {"type": "string", "pattern": "^f[1-9][0-9]?$"}},
    }, "required": ["taskClass", "reason", "domain", "sourceRefs", "viewRecipe", "task", "criteria"],
        "additionalProperties": False}
    reviewing = chr(10).join([
        "Look once at exact project sources and get findings back, never images: the bound Studio renders every frame itself",
        "and one provider call reports what is visible about your criteria. Use it for a spatial or formal task (massing,",
        "proportion, relations, composition, a sheet's hierarchy) after a meaningful batch. A deterministic edit (a value, a",
        "dimension, a count) is checked by readback, not looked at. When the user asks to see a view, read",
        "GET /api/drawings/model-view through studio_request instead.",
        "taskClass spatial_formal allows a first_bundle review, then one after_repair review whose addressedFindingIds name",
        "findings of the last review your repair answered. polish allows polishRounds polish_round reviews (1-4, more than 2",
        "only when the user's words in this message ask to keep refining, such as 继续优化 or 打磨). deterministic_edit allows none.",
        "Hub holds the allowance of the user message you are answering, fixes its class once a review is spent, and starts a",
        "new one with the user's next message.",
        "A modeling review names one model {kind: 'model', runId, stateDigest, assetSha256}, the result's non-null modelSource",
        "unchanged, with viewRecipe from front, back, left, right, top, axon. Board, drawing and render reviews name registered",
        "pages {kind: 'page', runId, assetSha256, revisionRef, pageIndex} exactly as GET /api/documents lists them, with",
        "viewRecipe page-<pageIndex> of each. criteria [{criterionId, text}] say what to inspect, preserve what must not be",
        "disturbed, and knownFacts are exact readback values (levels, clear sizes) the observer should not ask about again.",
        "A finding marked escalate touches a preserve condition: ask the user about it instead of repairing and reviewing again.",
        "Refusals spend nothing: VISUAL_BUDGET_EXHAUSTED, VISUAL_REVIEW_NOT_WARRANTED, VISUAL_REVIEW_OUT_OF_ORDER,",
        "VISUAL_SOURCE_MISMATCH (read the current exact source), VISUAL_PROVIDER_UNAVAILABLE. VISUAL_PROVIDER_FAILED spends the review.",
    ])
    # Keep common actions usable without a schema round trip. Detailed producer
    # contracts remain discoverable on demand; the agent chooses observation points.
    modelling = chr(10).join([
        "Use the bound project's Studio API. Lengths are metres; plan points are [x, z], with Y up.",
        "Read studio_schema for action details or producer inputs as needed.",
        "",
        "CURRENT STATE: GET /api/state and GET /api/state/frame provide stateDigest, components/elements and levels.",
        "To continue a retained candidate, read these with ?run=<candidateId> and send sourceRunId on writes.",
        "Keep its sourceStageRef when provided. Viewing a candidate alone does not change the editing base.",
        "An empty project can prepare a modeling base with POST /api/project/modeling {projectId}, then read state/frame.",
        "",
        "CREATE: POST /api/proposals/sketch with",
        "{stateDigest, componentId, elementId, profile: [[x,z], ...], height, baseLevel}.",
        "A closed profile does not repeat its first point. Reusing elementId updates that form.",
        "For several forms, use {stateDigest, sketches: [{componentId, elementId, profile, height, baseLevel}, ...]}.",
        "Items run in order; baseDatum: '<elementId>-top' can replace baseLevel to stack on an earlier form.",
        "sourceRunId, sourceStageRef, sourceProposalId and keep are top-level fields. A rejected batch saves nothing.",
        "New components need parentComponentId under a built component and semanticKind; existing components can hold generic forms.",
        "",
        "EDIT: POST /api/proposals/transform, /api/proposals/push-pull, /api/proposals/elevation or /api/proposals/delete.",
        "Read each action's schema for its fields; tool errors identify unsupported operations. Choose methods that preserve design meaning.",
        "For an existing numeric control, GET /api/capabilities/candidate.modify_existing?target=<componentId>&elementId=<the element>&run=<candidateId>",
        "returns its current values, units and a ready request; edit that body and POST /api/capabilities/{capabilityId}/run.",
        "GET /api/capabilities?goal=<user request> helps discover operations; an index miss does not exclude the other listed APIs.",
        "keep is a list of protected refs, e.g. ['entity:portico-base']. Use the actual target and source, not a guessed field.",
        "For linked dimensions, use POST /api/proposals with {stateDigest, semanticEdit: {summary, parameters: [...], entities: [...]}}.",
        "Use semanticEdit or utterance, not both. Existing omitted fields/dependencies are retained; revise upstream controls for linked edits.",
        "Read studio_schema POST /api/proposals with producer (e.g. prism, loft, wall, planar-surface) for the actual authoring contract.",
        "Geometry binds parameters with '@key'; formulas belong in parameters[].expr with inputs, and value must match the expression.",
        "GET /api/semantics supplies registered semantic_kind aliases; role.* and condition.* IDs are not those aliases.",
        "Keep early forms generic until their role is established. Existing object identity, hosted features and intended relationships matter when changing representation.",
        "",
        "COMPOSE / OBSERVE / CONTINUE:",
        "Chain known edits in memory by passing the last proposalId as sourceProposalId; keep stateDigest at the chain's original baseStateDigest.",
        "sourceRunId/sourceStageRef are inherited. GET /api/proposals/{id} reads accumulated changes; inspect conflicts before executing.",
        "MODEL CONVERSION: When asked to export/convert a model to 3DM, SKP, GLB or DWG, use POST /api/exports via studio_request. Do not use an export button or write a converter in the shell. Body: {targetFormat: 'glb', attachmentId: '<exact chat attachment id>'} for an upload, or {targetFormat: 'glb', projectRevision: {runId, stateDigest, assetSha256}} for the exact current project model. Read current project state/artifacts first; never substitute an upload for project state. If 'this model' could mean the project or an upload, or multiple uploads match, ask which model. Do not guess by filename or newest file. Read GET /api/exports/capabilities for supported routes. Submission returns jobId/statusPath; poll that statusPath with GET, report queued/running progress, and only on succeeded return its downloadUrl as a Markdown link with warnings. On failed/interrupted report failureReason, never invent a file link. For SKP/DWG without an available verified executor, say 当前没有配置可用的执行器; never describe the format as permanently unsupported. Installed software does not establish conversion capability. The backend chooses providers; users do not need to choose software. Same-format validated delivery is not a conversion. Do not use awaitSeconds on exports.",
        "At a useful design decision point, POST /api/proposals/{id}/candidate with no body and awaitSeconds: 60 beside method/path.",
        "This materializes a reversible, unaccepted candidate. Multiple observation and revision cycles can occur within the same Stage.",
        "After observing it, start further changes from GET /api/state?run=<candidateId>, using that stateDigest and sourceRunId;",
        "sourceProposalId continues an unexecuted chain, not a newly selected candidate base.",
        "The numeric capability run also accepts awaitSeconds: 60 with sourceRunId in its body. Waiting posts the change once.",
        "With awaitSeconds, candidate/artifacts/objects and compare, when present, are completed readbacks; reuse them for observation.",
        "Use an available artifact's non-null modelSource unchanged for visual_review and model-view. A missing source cannot be reconstructed from hashes.",
        "Follow next for missing reads and retain any source/state/context checks the task still requires.",
        "On timeout, follow the returned job/candidate reads; never send the request again merely to wait.",
        "GET /api/candidates/{id} returns retained objects with bbox.min/max, lengthUnit and upAxis, plus objectReadbackError if inspection is unavailable.",
        "GET /api/candidates/{id}/compare?against=<runId> compares with the required source run. The first candidate has no prior run to compare.",
        "Object bounds and successful checks are not visual inspection or proof of the user's spatial intent; for a spatial or formal task, check the result with visual_review and report gaps.",
        "For invalid input, use the named schema to correct it. For stale state/conflicts, refresh the exact source and reconcile the change while preserving keep conditions.",
        "A refused request made no model. Distinguish unsupported operations from correctable inputs; report unresolved limits without inventing success.",
        "",
        "ADMIT: when a task's loop is complete, POST /api/admissions once: {task: {kind: 'hub-chat'}, study: {id, label, baseRunId}",
        "(id an ASCII slug) for several alternatives built from one run, results: [{runId, outcome: 'admitted', supersedes: [attempt runIds it replaced], label}]}.",
        "outcome 'rejected' only where the user's words reject that result; add feedbackQuote with their exact passage.",
        "The chat fills messageSource and rawLanguage; never supply them. A refusal names each failing clause per run; an identical",
        "retry returns the same record. GET /api/admissions?include=rejected lists what is already admitted or tried.",
        "CONTINUE: only when the user's words ask to continue from a result, PUT /api/working-draft {runId, baseRevisionSha256}",
        "with revisionSha256 from GET /api/working-source, and feedbackQuote for their exact passage. It moves the Working Head",
        "and admits nothing; generating a result never moves it.",
        "",
        "OTHER READS: GET /api/project, /api/state/volumes, /api/program, /api/options, /api/board,",
        "/api/artifacts, /api/documents, /api/document-annotations, /api/jobs/{id}.",
        "OTHER ACTIONS: POST /api/state/closure, /api/program, /api/options, /api/options/{id}/select, /api/candidates/combine;",
        "CONTEXT READ: POST /api/intents/context compiles current task facts from projectId, stateDigest, utterance and exact sourceRunId/sourceStageRef; focus is optional.",
        "Repeat the same source/task/focus with contextRefs for omitted facts or contextOffset for the next reference index page. This reads only and grants no edits; use studio_schema for its full contract.",
        "For an explicitly selected precedent, add studyEvidence:[{studyId,ledgerRef}] (up to 3 exact revisions) to that context read. It returns the retained prior with conditions and counterevidence, not accepted design truth. Never infer that an older revision is current. If completeness is false or numerical details are needed, GET /api/studies/{studyId}?ledgerRef=<exact-ref> reopens that source; external citation summaries are not verified source text.",
        "RETAINED FEEDBACK: When the user gives an avoid/keep direction for later work, POST /api/decisions using its studio_schema, exact observed source and narrow stated scope. Save that feedback before continuing; do not turn an ordinary change request or your own judgment into a retained preference.",
        "The chat fills rawLanguage/messageSource from this actual user turn and sourceKind=agent for your interpretation. Never supply those fields, invent user approval or strengthen a soft preference into a hard rule. The user need not confirm an internal grant; the existing Runtime authorization still applies.",
        "GET /api/decisions reads retained feedback; GET /api/decisions/{id} reads its history. On the user's revocation request, POST /api/decisions/{id}/revisions with action=revoke and the revisionRef you read as expectedRevisionRef; the chat binds the reason and revisionMessageSource. This tool cannot supersede rules, save lock decisions, accept a Stage or unlock a parameter.",
        "Before drawing or writing artifact copy, read /api/intents/context with the actual Stage/targets/source; its default reads design and drawing decisions, and copy work selects decisionContext.domain=copy. Consume only scopedDecisions returned for that task, not every record in the decision list. Refresh after saving/revoking feedback or changing scope/source.",
        "Copy feedback targets copy:style; drawing feedback targets drawing:hatch, drawing:lineweight, drawing:beyond, drawing:entourage or drawing:poche. Only design uses targetRefs. For copy/drawing context omit targetRefs; omit decisionContext.source when no exact document/Board evidence is needed, rather than putting the outer Design source there. Copy evidence is document; drawing evidence is document or Board. The outer ContextPack still binds the current Design source.",
        "Carry applicable supported design keep refs into the existing edit's keep field and check the execution result. Preserve the actual relation or parameter asked for, not an entire unrelated object. Keep existing parameter locks; unsupported relation protection or hatch controls require explicit defer, not invented enforcement.",
        "Use each decision once for its relevant effect: preserve/filter for supported hard constraints, a generation preference for soft wording, or defer for unsupported effects. Inspect the next artifact and name any remaining gap; a context entry alone proves no behavior changed.",
        "PUT /api/board, /api/document-annotations. Use their schemas for exact inputs.",
        "DRAWINGS: POST /api/drawings/elevations automatically registers results in MonkeyDiagram's documents list.",
        "SECTION PERSPECTIVE (剖透视): POST /api/drawings/section-perspectives cuts the exact model with a section plane, removes the side the eye is on,",
        "and draws the kept side in true perspective: the cut filled (poché) and true to scale at 1:scaleDenominator, farther geometry smaller,",
        "lines perpendicular to the cut converging at the eye's point on it. Minimal body: {projectId, sourceStageRef or modelSource,",
        "section: {line: [[x1, y1], [x2, y2]], keep: 'left'|'right'}}. The line is a plan line with the same plan numbers as profile/wall points",
        "(the exact STEP's X/Y in its unit, Z up); keep is the side kept walking from the first point to the second; the eye stands on the other side.",
        "The default camera looks straight through the cut from 1.6 m above the lowest cut point, fitting the cut's width in 55 degrees.",
        "Optional: camera {eyeHeight, fovDeg} or {eye, target, up?, fovDeg?} (the target centres the frame; to move only the vanishing point,",
        "move the eye and keep the target at the cut's centre); section {origin, normal} for any plane (normal points toward the eye);",
        "depth, hiddenObjectIds, scaleDenominator (e.g. 50 for a room) and drawingId. Like elevations it registers the drawing in the documents list",
        "and returns that document; see it with POST /api/board/export using its runId, assetSha256, revisionRef and pageIndex 0.",
        "Refusals are named, e.g. SECTION_PLANE_MISSES_MODEL or SECTION_EYE_ON_KEPT_SIDE; correct the plane or camera rather than retrying.",
        "CUT PLAN: POST /api/drawings/plans makes or rebuilds a retained cut plan from modelSource or sourceStageRef (read its schema). To place entourage",
        "on a retained plan, send its previousRevisionRef with dressingOperations, one batch applied whole: {op: 'insert', id, object: {id, assetId: 'person-plan'|'tree-plan',",
        "positionUv: [u, v], size, flipped?, anchorObjectId?}}, {op: 'move', id, positionUv}, {op: 'scale', id, size}, {op: 'flip', id, flipped} or {op: 'delete', id}.",
        "Positions and sizes are in the source model's length unit; with anchorObjectId, positionUv is an offset from that object's projected centre.",
        "Each object keeps its id and stays editable on its own; a refused batch writes nothing. Add reason with the user's correction when one asked for it.",
        "GET /api/drawings/plans/vector?runId=&assetSha256=&revisionRef= reads the plan's SVG, symbols and anchor choices; POST /api/drawings/plans/status",
        "{runId, assetSha256, revisionRef} says whether it is current and which objects are missing or outside the view; GET /api/drawings/plans/dimensions lists",
        "the dimensions a plan can place. Drawing revisions never move the design. GET /api/drawings/corrections?projectId=[&drawingId=] reads how",
        "revisions changed and which repeated corrections the architect may save as a project recipe; only the architect can save one.",
        "SEE A VIEW: when the user asks to see a view, GET /api/drawings/model-view?runId=<id>&stateDigest=<digest>&assetSha256=<3dm sha256>&view=front returns an MCP image",
        "with exact source metadata. To judge a spatial or formal result, call visual_review instead: it answers findings, not images.",
        "Read modelSource from the awaited result's artifacts or the candidate's 3dm artifact. Views: front/back/left/right/top/axon (axon is isometric). This is a read-only line projection from complete retained STEP; unsupported sources refuse rather than show a proxy.",
        "GET /api/drawings/styles and POST /api/drawings/sheets compose a sheet from exact modelSource, styleId and scaleDenominator.",
        "Top is an orthographic projection, not a cut plan. GET /api/documents?runId=<runId> reads that run's drawings.",
        'DRAWING PAGE: POST /api/board/export is a read-only native MCP image: body {projectId, pages:[{runId, assetSha256, revisionRef, pageIndex}], format:"png", zip:false, maxEdge:2048}. Copy exact source fields from GET /api/documents or the generated drawing result; revisionRef must be explicit (null for sources without a revision), pageIndex is zero-based. One clean source page, no annotations, at most 2048 pixels per edge and 4 MiB; use smaller maxEdge if too large. No operationId or awaitSeconds.',
        "For page edits, read existing annotations and use baseRevisionSha256 with the exact run/asset/page/drawingRevisionRef.",
        "Board arranges document references; generated drawings are saved by their drawing API.",
        "Stage acceptance, formal issue and printer upload are separate from this tool's reversible design actions.",
    ])
    tools = [
        {"name": "chat_present", "description": _PRESENTATION_INSTRUCTIONS, "inputSchema": {
            **ChatPresentationRequest.model_json_schema(),
            "properties": {key: value for key, value in ChatPresentationRequest.model_json_schema()["properties"].items()
                           if key not in {"projectId", "sourceSessionId"}},
            "required": ["turnId", "messageId", "kind"] if external else ["messageId", "kind"],
        }},
        {"name": "studio_schema", "description": "Read the exact request/response schema of an allowed Studio action. "
         "Use it to discover inputs, clarify a field or correct a request. Paths may contain template segments, "
         "such as /api/proposals/{id}/candidate. For semantic authoring, supply producer to select its request inputs.", "inputSchema": schema_input},
        {"name": "studio_request", "description": modelling, "inputSchema": request_schema},
        {"name": "visual_review", "description": reviewing, "inputSchema": review_schema},
        {"name": "fab_request", "description": "Use MonkeyFab GET /api/fab/profiles or POST /api/fab/send for dry-run validation only. This tool never uploads or starts printing.", "inputSchema": input_schema},
        {"name": "attachment_read", "description": "Read an uploaded attachment from this conversation by its id. "
         "Returns UTF-8 text without NUL characters, or base64 for binary files. offset, limit, total and nextOffset "
         "count text characters or binary bytes; follow nextOffset until null. PDF page is 1-based and reads one page's "
         "extracted text, with totalPages for navigation. Empty PDF text does not mean the page image was inspected. "
         "This read-only tool does not start Studio or change project files.", "inputSchema": {
             "type": "object", "properties": {
                 "attachmentId": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "default": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 65536, "default": 32768},
                 "page": {"type": "integer", "minimum": 1, "default": 1},
             }, "required": ["attachmentId"], "additionalProperties": False,
         }},
        # Desktop automation is advertised on every machine and permitted on
        # none: its route answers a machine whose policy file does not enable
        # it with the file that would.
        *computer_tools.tool_definitions(),
    ]
    # Only the local stdio adapter reads an explicitly selected path. HTTP takes bytes/references only.
    tools[0]["inputSchema"]["properties"]["attachments"] = {"type": "array", "maxItems": 8, "items": {"anyOf": [
        {"$ref": "#/$defs/ChatAttachmentInput"},
        {"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"},
                                            "mimeType": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    ]}}
    if external:
        tools.insert(0, {"name": "presentation_bind", "description": "Connect this source session to its configured Hub project, "
                        "reusing the same conversation across turns/reconnects. Returns its URL and default presentation instructions.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method, params = request.get("method"), request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                          "serverInfo": {"name": "monkeyhub", "version": "0.1.0"}, "instructions": _PRESENTATION_INSTRUCTIONS}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                try:
                    name, arguments = params.get("name", ""), params.get("arguments", {})
                    if external and name == "presentation_bind":
                        if arguments:
                            raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "The connection already fixes its project and source session.")
                        presentation_binding = _request_json(hub, "/api/chat/presentation/bind", "POST", external.model_dump())
                        chat_id = presentation_binding["chatId"]
                        value = {key: value for key, value in presentation_binding.items() if key != "token"}
                        value["instructions"] = _PRESENTATION_INSTRUCTIONS
                    elif external and presentation_binding is None:
                        raise HubFailure(409, "CHAT_PRESENTATION_BIND_REQUIRED", "Call presentation_bind before publishing or using project tools.")
                    elif external and name == "chat_present":
                        value = _present_tool(hub, chat_id, arguments, presentation_binding["token"])
                    else:
                        value = call_tool(hub, chat_id, name, arguments)
                    arguments = params.get("arguments", {})
                    image_read = (params.get("name") == "studio_request"
                                  and (str(arguments.get("method", "GET")).upper(),
                                       urlsplit(arguments.get("path", "")).path) in {
                                           ("GET", "/api/drawings/model-view"), ("POST", "/api/board/export")})
                    if image_read:
                        metadata = {key: item for key, item in value.items() if key != "data"}
                        result = {"content": [
                            {"type": "text", "text": _redact(json.dumps(metadata, ensure_ascii=False))},
                            {"type": "image", "mimeType": value["mimeType"], "data": value["data"]},
                        ]}
                    else:
                        result = {"content": [{"type": "text", "text": _redact(json.dumps(value, ensure_ascii=False))}]}
                except HubFailure as exc:
                    # Keep the Runtime's refusal class across the MCP boundary.
                    # A stale base is not an input typo and must never be blindly retried.
                    failure = {"code": exc.error.code, "detail": _redact(exc.error.detail)[:1200],
                               "httpStatus": exc.status}
                    result = {"isError": True, "content": [{"type": "text", "text": json.dumps(failure, ensure_ascii=False)}]}
                except Exception as exc:
                    result = {"isError": True, "content": [{"type": "text", "text": _redact(str(exc))[:1500]}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, TypeError):
            continue


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", action="store_true", required=True)
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--chat-id")
    parser.add_argument("--project-dir")
    parser.add_argument("--source-session-id")
    parser.add_argument("--provider", choices=("codex", "claude", "coding-plan"), default="codex")
    parser.add_argument("--title", default="External conversation")
    args = parser.parse_args()
    if args.source_session_id and args.project_dir:
        external = ChatPresentationBindRequest(projectDir=args.project_dir, sourceSessionId=args.source_session_id,
                                               provider=args.provider, chatId=args.chat_id, title=args.title)
        _mcp(_url(args.hub_url), args.chat_id, external)
    elif args.chat_id and not args.source_session_id and not args.project_dir:
        _mcp(_url(args.hub_url), _identifier(args.chat_id))
    else:
        parser.error("Use --chat-id for a Hub-owned conversation, or --project-dir and --source-session-id for external presentation.")
