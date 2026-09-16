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
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Literal, Mapping
from urllib.error import HTTPError
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

from archflow.project.refs import ProjectRecordRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord
from archflow_studio_api.settings import read_application_settings

from .models import (
    ChatAttachment, ChatCreateRequest, ChatDesignContext, ChatDetail, ChatMessage, ChatPostRequest, ChatProject,
    ChatPermission, ChatPermissionOption, ChatPermissionRequest,
    ChatProjectRequest, ChatProvider, ChatSummary, ChatUsageSource, ChatWorkspace, HubError, HubFailure,
)
from .chat_trace import HubTurnObserver
from monkeymonitor.store import UsageLog

_trace_headers = ContextVar("hub_tool_trace_headers", default={})
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


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
    "mcp__monkeyhub__fab_request",
    "mcp__monkeyhub__attachment_read",
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
    # Records written before ACP retain the exact native CLI continuation path.
    transport: Literal["cli", "acp"] = "cli"
    acpSessionId: str | None = None
    acpDefaultModel: str | None = None


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
    return next((row.id for row in reversed(session.messages) if row.role == "user"), "turn")


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
    attachments: tuple[tuple[ChatAttachment, Path], ...] = ()


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
                    if message.status == "streaming":
                        message.status = "interrupted"
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
        with self._lock:
            self._load()
            return [ChatSummary.model_validate(row.model_dump()) for row in
                    sorted(self._sessions.values(), key=lambda item: item.updatedAt, reverse=True)
                    if row.archived == archived and (project_id is None or row.projectId == project_id)]

    def usage_sources(self) -> list[ChatUsageSource]:
        """Archiving hides a chat, not the usage of its bound native session."""
        with self._lock:
            self._load()
            sources = []
            for row in self._sessions.values():
                if row.provider != "codex":
                    continue
                identifier = row.acpSessionId if row.transport == "acp" else row.nativeSessionId
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
            return ChatDetail.model_validate(self._session(session_id).model_dump())

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
            if app_id in {"monkeyarch", "monkeydiagram", "monkeyboard"} and stopping:
                configured = project_dir if project_dir is not None else read_application_settings(self.runtime_root).project_dir
                target = str(Path(configured).resolve()) if configured else None
                if any(target is not None and os.path.normcase(self._sessions[key].projectDir) == os.path.normcase(target) for key in self._running):
                    raise HubFailure(409, "CHAT_RUNNING", "Stop the running chat before closing its design tools.")
            yield

    def post(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            if self._closing:
                raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
            if session.archived:
                raise HubFailure(409, "CHAT_ARCHIVED", "Restore this archived chat before sending another message.")
            if request.projectId != session.projectId or _project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This message belongs to a different project.")
            if session_id in self._running:
                raise HubFailure(409, "CHAT_RUNNING", "This chat is already responding.")
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
                                                attachments=[attachment for attachment, _ in attachments]))
            session.status, session.error, session.updatedAt = "running", None, _now()
            self._save(session, tuple(attachments))
            self._sessions[session_id] = session
            running = _Running(design_context=request.designContext,
                               attachments=tuple((attachment, self._attachment_path(session.id, attachment))
                                                 for attachment, _ in attachments), trace=HubTurnObserver(
                self.usage_log, _turn_id(session), session.projectId, session.provider, session.model,
            ))
            self._running[session_id] = running
            running.thread = threading.Thread(target=self._run, args=(session_id, content, running), daemon=True, name=f"hub-chat-{session_id[:8]}")
            running.thread.start()
            return self.get(session_id)

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
        tool_names = ("studio_schema", "studio_request", "fab_request", "attachment_read")
        mcp_servers["monkeyhub"] = {
            **mcp, "enabled": True, "required": True,
            "enabled_tools": list(tool_names),
            "tools": {name: {"approval_mode": "approve"} for name in tool_names},
        }
        return mcp_servers

    def _command(self, session: _SavedChat, attachments: tuple[tuple[ChatAttachment, Path], ...] = ()) -> tuple[list[str], dict[str, str]]:
        commands = self.commands if self.commands is not None else _cli_commands()
        kind = "codex" if session.provider == "codex" else "claude"
        environment = _claude_env() if kind == "claude" else dict(os.environ)
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
                       "--tools", "default", "--allowedTools", ",".join(_CLAUDE_APPROVED),
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"monkeyhub": mcp}})]
            command += ["--resume", session.nativeSessionId] if session.nativeSessionId else ["--session-id", session.id]
            if attachments:
                command += ["--input-format", "stream-json"]
            if model:
                command += ["--model", model]
        return command, environment

    def _acp_permission(self, session_id: str, request: dict) -> Future:
        future = Future()
        with self._lock:
            session = self._session(session_id)
            running = self._running.get(session_id)
            if self._closing or running is None or running.stop.is_set() or request.get("sessionId") != session.acpSessionId:
                future.set_result(None)
                return future
            permission = ChatPermission(
                id=str(uuid4()), title=_redact(str(request.get("toolCall", {}).get("title") or "Permission requested")),
                options=[ChatPermissionOption.model_validate(item) for item in request["options"]],
            )
            session.messages.append(ChatMessage(
                id=f"{_turn_id(session)}:permission:{permission.id}", role="tool", content=permission.title,
                createdAt=_now(), status="streaming", permission=permission,
            ))
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
                self._permissions.pop((session_id, permission_id), None)
                self._save(session)
                raise HubFailure(409, "CHAT_PERMISSION_EXPIRED", "This permission request is no longer waiting for a decision.") from None
            message.content += " · " + (option.name if option else "Cancelled")
            active = self._running[session_id]
            if active.trace:
                active.trace.permission(permission_id, completed=True)
            message.permission, message.status = None, "complete"
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
                identifier = f"{_turn_id(session)}:acp-answer"
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
        try:
            with self._lock:
                session = self._sessions[session_id]
                client = self._acp_sessions.get(session_id)
                self._acp_tools[session_id] = {}
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
            client.prompt(prompt, session.acpSessionId, session.model, connected, self.timeout_s, images=images)
            return None
        except AcpCancelled:
            running.stop.set()
            return None
        except Exception as exc:
            with self._lock:
                client = self._acp_sessions.pop(session_id, None)
            if client is not None:
                client.close()
            return HubError(code="CHAT_ACP_FAILED", detail=_redact(str(exc), environment)[:1000])

    def _run(self, session_id: str, content: str, running: _Running) -> None:
        error: HubError | None = None
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
                    "Use the connected monkeyhub tools for architectural changes and queries. "
                    "Project files are read-only: never edit project.json, HEAD, input, runs or records directly. "
                    "The studio_request description already states the usual actions, their fields and their units: "
                    "follow it and call them. Read a schema only for something it does not cover. "
                    "Compose the whole requested modeling chain as in-memory proposals using sourceProposalId, "
                    "then execute the final proposal once as a Stage checkpoint candidate. Do not export a candidate after every form. "
                    "Ordinary design actions use these connected contracts; source and environment searches are only for "
                    "a requested code investigation or an actual tool failure that needs diagnosis. "
                    "For an existing numeric control, use its documented modification flow instead of drawing it again. "
                    "Author shared dimensions and dependencies through semanticEdit when the design needs linked changes; "
                    "later change the retained parameters together rather than recalculate and redraw every dependent object. "
                    "Read only what you do not already know from this conversation; do not re-read to confirm "
                    "what you have just been told. "
                    "Choose reasonable, reversible defaults for sizes nobody stated rather than stopping to ask, "
                    "and say plainly which numbers you chose. Ask at most one short question, and only when the answer "
                    "would change the design itself — never for internal ids, digests or payload shapes, which are yours to read. "
                    "Do not call another model via /api/intents. "
                    "If an action fails, say what it refused and what can be done now; never describe a candidate that was not made. "
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
                prompt += prepared
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
            prompt_input = prompt
            if session.provider != "codex" and running.attachments:
                blocks = [{"type": "text", "text": prompt}]
                blocks.extend({"type": "image", "source": {"type": "base64", "media_type": attachment.mimeType,
                               "data": base64.b64encode(path.read_bytes()).decode("ascii")}}
                              for attachment, path in running.attachments if attachment.mimeType in _IMAGE_MIMES)
                prompt_input = json.dumps({"type": "user", "session_id": session.nativeSessionId or session.id,
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
                        event_error, finished = self._event(session, event, environment)
                        completed_turn = completed_turn or finished
                        if event_error:
                            error = event_error
                        if time.monotonic() - last_save >= 0.3:
                            self._save(session)
                            last_save = time.monotonic()
                process.wait()
            finally:
                timer.cancel()
                if process.poll() is None:
                    _stop_process(process)
                process.wait(timeout=10)
                feeder.join(timeout=1)
                errors.join(timeout=1)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if not running.stop.is_set():
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
            with self._lock:
                session = self._sessions[session_id]
                self._clear_permissions(session_id)
                self._acp_tools.pop(session_id, None)
                session.status = "interrupted" if running.stop.is_set() else "failed" if error else "idle"
                session.error = HubError(code="CHAT_STOPPED", detail="The response was stopped.") if running.stop.is_set() else error
                for message in session.messages:
                    if message.status == "streaming":
                        message.status = "interrupted" if running.stop.is_set() else "failed" if error else "complete"
                session.updatedAt = _now()
                if running.trace:
                    running.trace.bind(session.acpSessionId if session.transport == "acp" else session.nativeSessionId)
                    running.trace.finish("cancelled" if running.stop.is_set() else "failed" if error else "succeeded")
                try:
                    self._save(session)
                finally:
                    self._running.pop(session_id, None)

    def _tool_message(self, session: _SavedChat, item: Mapping, kind: str, environment) -> None:
        """Keep one visible row per MCP call, from started to its outcome."""
        content, candidate, failed = _tool_activity(item, environment)
        message_id = f"{_turn_id(session)}:{item.get('id') or 'tool'}"
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
        elif kind == "user":
            for row in event.get("message", {}).get("content", []) or []:
                if isinstance(row, dict) and row.get("type") == "tool_result":
                    identifier = f"{_turn_id(session)}:{row.get('tool_use_id') or 'tool'}"
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
            output_id = f"{_turn_id(session)}:{message_id}"
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
            self._session(session_id)
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
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


_READ = re.compile(r"^/api/(project|state(?:/frame|/volumes)?|semantics|program|options|board|artifacts|documents|document-annotations|drawings/styles|capabilities(?:/[A-Za-z0-9_.-]+)?|proposals/[A-Za-z0-9_-]+|jobs/[A-Za-z0-9_-]+|candidates/[A-Za-z0-9_-]+(?:/compare)?)$")
_POST = re.compile(r"^/api/(project/modeling|state/closure|capabilities/[A-Za-z0-9_.-]+/run|proposals|proposals/(sketch|transform|push-pull|delete)|proposals/[A-Za-z0-9_-]+/candidate|program|options|options/[A-Za-z0-9_-]+/select|candidates/combine|drawings/(elevations|sheets))$")
_WRITE = re.compile(r"^/api/(board|document-annotations)$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The bound service redirected the request.")


def _url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.username or url.password or url.query or url.fragment:
        raise HubFailure(422, "CHAT_SERVICE_INVALID", "Only the bound local application can be called.")
    return f"http://{url.netloc}"


def _request_json(base: str, path: str, method: str = "GET", body=None, timeout: float = 180, *, headers=None):
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


def _together(calls: Mapping[str, tuple], timeout: float) -> dict:
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
        if isinstance(answer, BaseException):
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
    if studio.get("state") != "running" or not studio.get("url") or not studio.get("processId"):
        raise HubFailure(409, "CHAT_STUDIO_UNAVAILABLE", "Open MonkeyArch for this project before using a design tool.")
    base = _url(studio["url"])
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
    "against the same base; target lists that element's current numbers, units and whether each can "
    "move; request is the capability's own body already holding those current values — a template to "
    "edit, never a change that was asked for or approved. contextTier, escalation, context and "
    "preflight are how the record reads these words and what it already answers about them. "
    "Because these are here, the usual state and schema lookups about this object are unnecessary; "
    "read further only for something they do not cover."
)


def _prepared_context(hub: str, chat_id: str, content: str, selected: ChatDesignContext,
                      stop: threading.Event, deadline: float) -> str | None:
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


def _context_pack(hub: str, chat_id: str, content: str, selected: ChatDesignContext, deadline: float) -> str:
    """This turn's selected-source context, as a paragraph to append to its prompt.

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
            "sourceRunId": selected.sourceRunId,
            "stateDigest": selected.stateDigest,
            "targetComponentId": selected.targetComponentId,
            "elementId": selected.elementId,
            **({} if selected.sourceStageRef is None else {"sourceStageRef": selected.sourceStageRef}),
        }, timeout=deadline - time.monotonic())
    finally:
        # The headers belong to the turn that set them and to nothing after it.
        _trace_headers.reset(token)
    return "\n\n" + _CONTEXT_NOTE + "\n" + _redact(json.dumps(pack, ensure_ascii=False))


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
    try:
        answers = _together(reads, left())
    except (HubFailure, OSError, TimeoutError) as cause:
        # The run finished; reading it back did not. Both facts travel, and
        # neither is allowed to stand in for the other.
        return {**started, "status": "succeeded", "readback": "failed",
                "detail": f"the run finished and could not be read back: {_reason(cause)}",
                "next": follow}
    candidate, comparison = answers["candidate"], answers.get("compare")
    return {
        **started,
        "status": "succeeded",
        "readback": "ok",
        "candidate": {
            key: candidate.get(key) for key in
            ("candidateId", "stateDigest", "changedVsProjection", "seatExecutionComplete",
             "relationChecks", "harness", "honesty")
            if key in candidate
        },
        # What the run saved, by the fields that say whether it is really there.
        "artifacts": [
            {key: row.get(key) for key in
             ("runId", "fileName", "relativePath", "representation", "lengthUnit",
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


def _call_tool(hub: str, chat_id: str, name: str, arguments: dict):
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
    method, path = str(arguments.get("method", "GET")).upper(), arguments.get("path", "")
    parsed = urlsplit(path)
    allowed = {"GET": _READ, "POST": _POST, "PUT": _WRITE}
    if "producer" in arguments and name != "studio_schema":
        raise HubFailure(422, "CHAT_TOOL_INVALID", "producer selects an authoring schema; it belongs to studio_schema.")
    if "operationId" in arguments and (name != "studio_request" or method == "GET"):
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
        if parsed.path == "/api/proposals":
            body["projectId"] = session["projectId"]
    comparison = body or {}
    if wait is not None and checkpoint:
        proposal = _request_json(base, parsed.path.removesuffix("/candidate"))
        comparison = {"sourceRunId": proposal.get("sourceRunId")}
    if method in {"POST", "PUT"}:
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
    if wait is None:
        if method == "POST" and parsed.path in {
            "/api/proposals", "/api/proposals/sketch", "/api/proposals/transform",
            "/api/proposals/push-pull", "/api/proposals/delete",
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
        return started
    # One POST has happened. From here on this call only reads.
    return _finish(base, started, comparison, time.monotonic() + wait)


def _mcp(hub: str, chat_id: str) -> None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
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
            "awaitSeconds": {
                "type": "integer", "minimum": 1, "maximum": _AWAIT_MAX_S,
                "description": "This tool's own option, beside method/path/body and never inside the "
                               "body; 60 suits an ordinary change. It is not an execution wrapper's "
                               "yield_time_ms: through functions.exec, omit yield_time_ms so it keeps its "
                               "30000 ms default, never set 1000, and never loop short functions.wait "
                               "calls. For the final POST /api/proposals/{id}/candidate (no body; the proposal supplies its source), "
                               "or POST " + _FINISHABLE + ", whose body must name sourceRunId: wait this many seconds, counted from when that action is "
                               "accepted, then answer with the job, the candidate and the comparison "
                               "against the run it was made from. Running out of time answers with the job "
                               "and candidate to read; the request is never sent twice. Any other path is "
                               "refused, so an answer never looks waited-for when it was not.",
            },
        }, "required": ["method", "path"], "additionalProperties": False,
    }
    # What the connected CLI is told it can do. The common actions are stated
    # here with their fields and units so that making something is a call, not
    # an exploration: a schema lookup is for what this does not cover.
    modelling = chr(10).join([
        "Call the bound project's existing Studio API. Lengths are metres, plan points are [x, z] pairs,",
        "and a height rises from the base the request names.",
        "",
        "MAKE FORMS (simple independent shapes use sketch; linked dimensions use semanticEdit below):",
        "1. GET /api/state -> stateDigest, components[].componentId, elements[].",
        "   If this project has no modeling component yet, POST /api/project/modeling with {projectId}",
        "   prepares its empty modeling base through the project API; then read the state again.",
        "   This creates no geometry or candidate and never replaces existing design inputs.",
        "2. GET /api/state/frame -> levels[].levelId, for the base to stand on.",
        "3. Plan the requested forms together. Prefer ONE POST /api/proposals/sketch with",
        "   {stateDigest, sketches: [{componentId, elementId, profile: [[x,z], ...], height, baseLevel}, ...]}.",
        "   The items run in order in memory, so a later item may stack on an earlier item with baseDatum.",
        "   sourceRunId/sourceStageRef/sourceProposalId and keep belong at the top level. A failed batch saves nothing.",
        "   For just one form the existing {stateDigest, componentId, elementId, profile, height, baseLevel} body also works.",
        "   Use baseDatum: '<elementId>-top' instead of baseLevel to stack on something already there.",
        "   elementId is yours to choose; sending the same elementId again changes that outline or height",
        "   instead of adding another form. Optional: summary, keep (refs this must not change), sourceRunId.",
        "4. Continue the WHOLE requested modeling chain in memory: pass the last proposalId as sourceProposalId",
        "   on each further sketch/transform/push-pull/delete/numeric proposal. Keep stateDigest equal to the first",
        "   proposal's baseStateDigest; sourceRunId and sourceStageRef are inherited. GET /api/proposals/{id}",
        "   reads the accumulated changes. Stacking with baseDatum can refer to forms created earlier in this chain.",
        "5. Only after every requested change is composed, POST /api/proposals/{id}/candidate ONCE using the last proposalId,",
        "   with awaitSeconds: 60 beside method/path and no body. This saves one final Stage checkpoint candidate.",
        "   It remains reversible and unaccepted; do not accept or issue a Stage. Never generate an intermediate",
        "   candidate just to continue the next step. If the wait runs out, follow its job/candidate reads; never send the request again.",
        "   A completed result includes objects with actual retained bbox.min/max, lengthUnit and upAxis; use those to report sizes.",
        "   A first candidate has compare: null because there is no prior run. objectReadbackError states missing inspection.",
        "   GET /api/candidates/{id} also returns these objects. No separate inspection/schema search is needed.",
        "A new component needs parentComponentId (an existing component a seat builds) and semanticKind (what it is);",
        "drawing under a component no seat builds is refused with the list of the ones that are built.",
        "",
        "REUSABLE DIMENSIONS AND DEPENDENCIES (same proposal and final checkpoint; no second model):",
        "POST /api/proposals with {stateDigest, semanticEdit: {summary, parameters: [...], entities: [...]}}.",
        "Use semanticEdit OR utterance, never both. sourceRunId/sourceStageRef/sourceProposalId and keep stay at the top level.",
        "For a new Component@1, fields.semantic_kind takes a registered alias: 'building' for the whole, 'cover' for a roof,",
        "or 'support' for a load-bearing column. Choose a fitting alias from GET /api/semantics; do not invent one.",
        "The returned role.* and condition.* IDs are not aliases for semantic_kind. Describe the specific object in fields.intent.",
        "An authored parameter is {key: 'width', value: 3, unit: 'm', epistemic_status: 'declared'}.",
        "A derived parameter adds expr and inputs, for example {key: 'left', value: -1.5, unit: 'm',",
        "expr: '-width / 2', inputs: ['width']}. Use ordinary arithmetic with parameter names; value must match the expression.",
        "For a centered width, similarly derive 'right' as 'width / 2' with value 1.5 and inputs ['width'].",
        "A prism entity is {entity_id: 'canopy', schema: 'Element@1', parent_id: '<existing componentId>',",
        "fields: {component_id: '<same componentId>', producer: 'prism', references: {base: {level: '<levelId>'}},",
        "params: {profile: [['@left',0],['@right',0],['@right',1.8],['@left',1.8]], height: 0.18, elevation: 2.8}}}.",
        "Bind each dimension that must move together with '@key', including profile coordinates, height and elevation.",
        "Expressions belong in parameters[].expr; an '@key' in geometry references a parameter, not an inline expression.",
        "Later, read GET /api/state?run=<candidateId> once: parameters include retained values, expr and inputs.",
        "Change several independent controls in ONE semanticEdit: {summary: '...', parameters: [{key: 'width', value: 4.2}, ...]}.",
        "Existing omitted fields and dependencies are preserved. Change upstream controls; do not overwrite derived formulas",
        "or rebuild every dependent form. Submit the final proposal once with awaitSeconds: 60 as above.",
        "If a semantic field is unclear, read studio_schema POST /api/proposals with producer: 'prism' (or the producer needed).",
        "That selects its request contract; avoid repeatedly reading the full schema with unrelated producers and response payloads.",
        "When a wall is explicitly requested, producer: 'wall' accepts references.line.from/to as {point: [x,z]},",
        "including @parameter coordinates, or existing grid/host references; references.base names an existing level.",
        "Its params include thickness, height and optional openings [{opening_id, kind, along, width, sill, head}].",
        "Read studio_schema POST /api/proposals with producer: 'wall' for its exact contract. No invented GridAxis is needed.",
        "Keep an existing wall's identity, hosted openings and relations; do not silently replace it with a prism.",
        "A shape that has not been identified as a wall can remain a generic form; geometry alone does not decide its role.",
        "RECOVER REFUSED MODELING REQUESTS: preserve the original source/base, keep conditions and object identity.",
        "For SEMANTIC_EDIT_INVALID or REQUEST_INVALID, inspect the named contract, correct the request, and continue",
        "from the last valid sourceProposalId before the single final checkpoint. A rejected request produced no model.",
        "For STALE_BASE or a chain conflict, re-read the actual state and reconcile the intended change; never blindly retry",
        "or drop keep conditions. Unknown references must be corrected explicitly, not converted into free coordinates.",
        "A truly unsupported operation is a capability limit. Use another representation only when it preserves the",
        "requested design meaning and relationships; otherwise explain the limitation rather than invent success.",
        "For a tapered or rounded form, use ONE entity with producer: 'loft' instead of stacked independent prisms.",
        "Its params are {profiles: [[[x,y,z],...], ...], profile_size: <vertices per section>, loft_type: 'normal',",
        "profile_basis: 'polyline', cap_ends: true}; references.base names the level or datum. Y is height here.",
        "Use at least two closed sections with equal vertex counts and no repeated closing point; coordinates may use '@key'.",
        "loft_type may be 'normal' or 'straight'. The loft publishes no top datum; its shape follows the actual sections.",
        "Proposal creation returns the checked change and impact without echoing the submitted edits and operator twice;",
        "Large changes/direct lists show their total and omitted counts; detailsPath reads the full proposal.",
        "Conflicts, locks, keep conditions and coverage limitations stay complete. GET /api/proposals/{id} retains all details.",
        "",
        "CHANGE SOMETHING THAT IS ALREADY THERE:",
        "For a chain of changes, use POST /api/proposals (numeric edits), /api/proposals/transform,",
        "/api/proposals/push-pull or /api/proposals/delete with sourceProposalId; read that action's schema when needed.",
        "Compose all steps before the one final checkpoint above. The immediate capability run below is only",
        "for a SINGLE requested change that is already the complete task, never for an intermediate chain step.",
        "A. READ ONCE. If the target and the run are already known from this conversation:",
        "   GET /api/capabilities/candidate.modify_existing?target=<componentId>&elementId=<the element>",
        "   &run=<the candidate being worked on> -> the numbers on that element that can move with their",
        "   current values and units, the refs a keep clause may name, and the exact request to send next",
        "   with this project's base already in it. That is the whole preparation; do not read the index",
        "   or the state again to confirm what you already have.",
        "   If the object is not known yet, find it first — GET /api/state lists the elements and the",
        "   components they belong to, and GET /api/capabilities?goal=<what the user asked, in their own",
        "   words> says which capability serves it. Never guess a target from the wording of the request,",
        "   and never take the first field of a component as the one that was meant.",
        "B. RUN ONCE. POST /api/capabilities/{capabilityId}/run with the body that answer handed back,",
        "   adding awaitSeconds: 60 beside method/path/body. It is this tool's own option, never a field",
        "   of the body; 1-180 is allowed and 60 suits an ordinary change. The answer is the finished",
        "   result: the job, the candidate with what it exported, and the comparison object by object",
        "   against the run it was made from, which the body must name as sourceRunId. keep is a list such as",
        "   ['entity:portico-base']; a change that reaches something kept comes back refused and runs nothing.",
        "   targetComponentId is the element's own component. A read selects a base with ?run=<runId>;",
        "   a write names it as sourceRunId in the body.",
        "   Without awaitSeconds the call answers {proposalId, jobId, candidateId} and the job, candidate",
        "   and comparison are the ordinary reads below. If the wait runs out, the answer names the job and",
        "   the candidate to read: pick those up, never send the request again.",
        "   awaitSeconds is not an execution wrapper's yield_time_ms; they are two different waits.",
        "   If you call this through functions.exec, omit yield_time_ms so it keeps its 30000 ms default.",
        "   Do not set yield_time_ms: 1000, and do not loop short functions.wait calls.",
        "   If it is still running after that default wait, continue waiting on the same call with",
        "   ordinary-length waits; the request is already in and must never be sent again.",
        "No match in the index is not a verdict that the system cannot do it: the answer says so, names",
        "what matched, and the actions below remain available.",
        "",
        "READ: GET /api/capabilities, /api/state, /api/state/frame, /api/state/volumes, /api/semantics, /api/program, /api/options, /api/board,",
        "/api/artifacts, /api/documents, /api/document-annotations, /api/proposals/{id}, /api/jobs/{id}, /api/candidates/{id}.",
        "GET /api/candidates/{id}/compare?against=<runId> — the run to compare with is required, and it is the",
        "sourceRunId the candidate was made from (the run before it); without it the request is refused.",
        "OTHER EXISTING ACTIONS: POST /api/state/closure, /api/program, /api/options, /api/options/{id}/select,",
        "/api/candidates/combine, /api/drawings/elevations; PUT /api/board, /api/document-annotations.",
        "POST /api/drawings/elevations generates and registers the drawing in MonkeyDiagram's documents list automatically.",
        "Views include front/back/left/right and top (an orthographic top projection, not a cut plan).",
        "For a composed sheet, GET /api/drawings/styles then POST /api/drawings/sheets with the exact modelSource,",
        "styleId and explicit scaleDenominator. It composes front/right/top in the selected style and returns a registered PDF.",
        "Choose hiddenObjectIds or outlineObjectIds only for requested display simplification; no model object is changed.",
        "Read GET /api/documents?runId=<the drawing's runId> to inspect its retained document entries.",
        "For page text or dimensions, read studio_schema for PUT /api/document-annotations and GET the existing page first.",
        "Preserve existing annotations, use its current baseRevisionSha256, and bind the exact run/asset/page/drawingRevisionRef.",
        "Do not use PUT /api/board to save a generated drawing. Board layout is a separate action, only when the user asks for it.",
        "Typed semanticEdit uses the same checked proposal path. No freeform intent/model call, issue, approvals or filesystem writes.",
    ])
    tools = [
        {"name": "studio_schema", "description": "Read the exact request/response schema of an allowed Studio action. "
         "The usual actions are already described in studio_request with their fields and units: use this only for an action "
         "that description does not cover, or a field it does not state. A path may be written with its template segments, "
         "such as /api/proposals/{id}/candidate. For semantic authoring, supply producer to select that producer's request inputs.", "inputSchema": schema_input},
        {"name": "studio_request", "description": modelling, "inputSchema": request_schema},
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
    ]
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method, params = request.get("method"), request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}}, "serverInfo": {"name": "monkeyhub", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                try:
                    value = call_tool(hub, chat_id, params.get("name", ""), params.get("arguments", {}))
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
    parser.add_argument("--chat-id", required=True)
    args = parser.parse_args()
    _mcp(_url(args.hub_url), _identifier(args.chat_id))
