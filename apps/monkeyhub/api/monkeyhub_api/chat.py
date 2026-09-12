"""Project-bound conversations backed by the installed coding CLIs.

The CLI retains its native conversation. Hub retains the visible transcript and
that exact session id under its configured runtime root. Design writes stay on
the existing Studio interfaces, reached through the small stdio tool below.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
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
    ChatCreateRequest, ChatDetail, ChatMessage, ChatPostRequest, ChatProject,
    ChatProjectRequest, ChatProvider, ChatSummary, ChatWorkspace, HubError, HubFailure,
)


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
# built-in names are the CLI's own; the three prefixed ones are this adapter's
# bound tools, which are the only MCP tools it is given at all.
_CLAUDE_APPROVED = (
    "Read", "Glob", "Grep", "Write", "Edit", "Bash", "TodoWrite",
    "mcp__monkeyhub__studio_schema",
    "mcp__monkeyhub__studio_request",
    "mcp__monkeyhub__fab_request",
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


class _SavedChat(ChatDetail):
    nativeSessionId: str | None = None


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
    def __init__(self, runtime_root: Path, hub_url: str, *, applications=None, commands=None, timeout_s: float = 900):
        self.root = runtime_root / "chats"
        self.runtime_root = runtime_root
        self.hub_url = hub_url
        self.applications = applications
        self.commands = commands
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self._sessions: dict[str, _SavedChat] = {}
        self._running: dict[str, _Running] = {}
        self._loaded = False
        self._closing = False
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
                self._save(session)
        self._loaded = True

    def _save(self, session: _SavedChat) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session.id}.json"
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(session.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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
            current = read_application_settings(self.runtime_root).project_dir
            if current:
                try:
                    project_id, project_dir = _project(current)
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

    def list(self, project_id: str | None = None) -> list[ChatSummary]:
        with self._lock:
            self._load()
            return [ChatSummary.model_validate(row.model_dump()) for row in
                    sorted(self._sessions.values(), key=lambda item: item.updatedAt, reverse=True)
                    if project_id is None or row.projectId == project_id]

    def _session(self, session_id: str) -> _SavedChat:
        self._load()
        _identifier(session_id)
        if session_id not in self._sessions:
            raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
        return self._sessions[session_id]

    def get(self, session_id: str) -> ChatDetail:
        with self._lock:
            return ChatDetail.model_validate(self._session(session_id).model_dump())

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
    def application_lifecycle(self, app_id: str, *, stopping: bool = False):
        with self._lock:
            if app_id in {"monkeyarch", "monkeydiagram", "monkeyboard"} and self._running:
                if stopping:
                    raise HubFailure(409, "CHAT_RUNNING", "Stop the running chat before closing its design tools.")
                configured = read_application_settings(self.runtime_root).project_dir
                target = str(Path(configured).resolve()) if configured else None
                if any(self._sessions[key].projectDir != target for key in self._running):
                    raise HubFailure(409, "CHAT_PROJECT_BUSY", "The design tools must use the running chat's project.")
            yield

    def post(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            if self._closing:
                raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
            if request.projectId != session.projectId or _project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This message belongs to a different project.")
            if session_id in self._running:
                raise HubFailure(409, "CHAT_RUNNING", "This chat is already responding.")
            if any(self._sessions[key].projectDir != session.projectDir for key in self._running):
                raise HubFailure(409, "CHAT_PROJECT_BUSY", "Another project has a running chat. Stop it before switching projects.")
            if self.applications is not None and self.applications.status("monkeyarch").state in {"starting", "running", "stopping"}:
                configured = read_application_settings(self.runtime_root).project_dir
                if not configured or str(Path(configured).resolve()) != session.projectDir:
                    raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The running Studio belongs to another project.")
            provider = next(row for row in self.providers() if row.id == session.provider)
            if not provider.available:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE", provider.detail)
            content = _redact(request.content.strip(), _claude_env())
            if not content:
                raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Enter a message.")
            if not session.messages and session.title == "New chat":
                session.title = content.splitlines()[0][:80]
            session.messages.append(ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=_now()))
            session.status, session.error, session.updatedAt = "running", None, _now()
            self._save(session)
            self._sessions[session_id] = session
            running = _Running()
            self._running[session_id] = running
            running.thread = threading.Thread(target=self._run, args=(session_id, content, running), daemon=True, name=f"hub-chat-{session_id[:8]}")
            running.thread.start()
            return self.get(session_id)

    def _command(self, session: _SavedChat) -> tuple[list[str], dict[str, str]]:
        commands = self.commands if self.commands is not None else _cli_commands()
        kind = "codex" if session.provider == "codex" else "claude"
        environment = _claude_env() if kind == "claude" else dict(os.environ)
        bridge = [str(Path(__file__).resolve()), "--mcp", "--hub-url", self.hub_url, "--chat-id", session.id]
        mcp = {"command": sys.executable, "args": bridge}
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
            listing = subprocess.run(
                [*commands[kind], "-C", session.projectDir, "mcp", "list", "--json"],
                cwd=session.projectDir, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                servers = json.loads(listing.stdout)
                if listing.returncode or not isinstance(servers, list):
                    raise ValueError("invalid MCP listing")
                # Disabled overrides must still declare a valid transport of
                # the original kind. Values stay local and contain no saved
                # server credentials.
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
            # exec cannot display an MCP approval prompt. Authorize only the
            # three project-bound actions this adapter exposes for the turn;
            # shell/project files retain the read-only sandbox below.
            tool_names = ("studio_schema", "studio_request", "fab_request")
            mcp_servers["monkeyhub"] = {
                **mcp, "enabled": True, "required": True,
                "enabled_tools": list(tool_names),
                "tools": {name: {"approval_mode": "approve"} for name in tool_names},
            }
            command = [*commands[kind], "exec", "--json", "--skip-git-repo-check",
                       "-C", workdir, "-s", "workspace-write", "--color", "never",
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       "-c", f"mcp_servers={_toml_value(mcp_servers)}"]
            if model:
                command += ["-m", model]
            if session.nativeSessionId:
                command += ["resume", session.nativeSessionId]
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
            if model:
                command += ["--model", model]
        return command, environment

    def _run(self, session_id: str, content: str, running: _Running) -> None:
        error: HubError | None = None
        stderr: list[str] = []
        completed_turn = False
        try:
            with self._lock:
                session = self._sessions[session_id]
                prompt = (
                    "You are the assistant in MonkeyHub. Reply in the user's language. "
                    "Use the connected monkeyhub tools for architectural changes and queries. "
                    "Project files are read-only: never edit project.json, HEAD, input, runs or records directly. "
                    "The studio_request description already states the usual actions, their fields and their units: "
                    "follow it and call them. Read a schema only for something it does not cover. "
                    "To make a form that is not there yet: state, frame, /api/proposals/sketch, then its "
                    "candidate route. To change something already modelled, do not draw it again: describe "
                    "the target through /api/capabilities and send the request it hands back with "
                    "awaitSeconds, which finishes it in that one call. Read only what you do not already "
                    "know from this conversation; do not re-read to confirm what you have just been told. "
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
            command, environment = self._command(session)
            if running.stop.is_set():
                return
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
                    process.stdin.write(prompt)
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
            timer = threading.Timer(self.timeout_s, lambda: _stop_process(process) if process.poll() is None else None)
            timer.daemon = True
            timer.start()
            started, last_save = time.monotonic(), 0.0
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
                if time.monotonic() - started >= self.timeout_s:
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
                session.status = "interrupted" if running.stop.is_set() else "failed" if error else "idle"
                session.error = HubError(code="CHAT_STOPPED", detail="The response was stopped.") if running.stop.is_set() else error
                for message in session.messages:
                    if message.status == "streaming":
                        message.status = "interrupted" if running.stop.is_set() else "failed" if error else "complete"
                session.updatedAt = _now()
                try:
                    self._save(session)
                finally:
                    self._running.pop(session_id, None)

    def _tool_message(self, session: _SavedChat, item: Mapping, kind: str, environment) -> None:
        """Keep one visible row per MCP call, from started to its outcome."""
        content, candidate, failed = _tool_activity(item, environment)
        message_id = f"{_turn_id(session)}:{item.get('id') or 'tool'}"
        message = next((row for row in session.messages if row.id == message_id), None)
        running = kind == "item.started" and str(item.get("status") or "") not in {"completed", "failed"}
        outcome = "streaming" if running else "failed" if failed else "complete"
        if message is None:
            message = ChatMessage(id=message_id, role="tool", content=content, createdAt=_now(), status=outcome)
            session.messages.append(message)
        elif not running or message.status == "streaming":
            # A repeated started event never reopens a call that already ended.
            message.content, message.status = content, outcome
        if candidate:
            message.candidateId = candidate
        session.updatedAt = _now()

    def _event(self, session: _SavedChat, event: dict, environment) -> tuple[HubError | None, bool]:
        kind = event.get("type")
        native = event.get("thread_id") if kind == "thread.started" else event.get("session_id")
        if isinstance(native, str):
            session.nativeSessionId = _identifier(native)
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
        if process is not None:
            _stop_process(process)
        if thread is not None:
            thread.join(timeout=10)
        return self.get(session_id)

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
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


_READ = re.compile(r"^/api/(project|state(?:/frame|/volumes)?|semantics|program|options|board|artifacts|capabilities(?:/[A-Za-z0-9_.-]+)?|proposals/[A-Za-z0-9_-]+|jobs/[A-Za-z0-9_-]+|candidates/[A-Za-z0-9_-]+(?:/compare)?)$")
_POST = re.compile(r"^/api/(state/closure|capabilities/[A-Za-z0-9_.-]+/run|proposals|proposals/sketch|proposals/[A-Za-z0-9_-]+/candidate|program|options|options/[A-Za-z0-9_-]+/select|candidates/combine|drawings/elevations)$")
_WRITE = re.compile(r"^/api/board$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The bound service redirected the request.")


def _url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.username or url.password or url.query or url.fragment:
        raise HubFailure(422, "CHAT_SERVICE_INVALID", "Only the bound local application can be called.")
    return f"http://{url.netloc}"


def _request_json(base: str, path: str, method: str = "GET", body=None, timeout: float = 180):
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
    request = Request(_url(base) + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            payload = json.load(exc)
            detail = payload.get("detail", "The application refused the request.")
        except (ValueError, AttributeError):
            detail = "The application refused the request."
        raise HubFailure(exc.code, "CHAT_TOOL_FAILED", _redact(str(detail))[:1200]) from exc


def _together(calls: Mapping[str, tuple], timeout: float) -> dict:
    """Ask for several independent things at once, and wait for all of them.

    Only calls that do not depend on each other are passed here, and the pool
    lives and dies inside this function: there is no worker layer, and nothing
    is queued across requests. A refusal is raised in the order the caller
    listed the calls, so the answer a client gets does not depend on which
    reply happened to lose the race.
    """

    from concurrent.futures import ThreadPoolExecutor

    ends = time.monotonic() + timeout
    pool = ThreadPoolExecutor(max_workers=max(1, len(calls)))
    try:
        pending = {name: pool.submit(_request_json, *call, timeout=timeout) for name, call in calls.items()}
        answers = {}
        for name, future in pending.items():
            try:
                # Waiting is bounded by the same deadline the calls are: a
                # thread that outlives it is abandoned rather than waited on.
                answers[name] = future.result(timeout=max(0.0, ends - time.monotonic()))
            except BaseException as cause:  # noqa: BLE001 - re-raised below, in order
                answers[name] = cause
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for name, answer in answers.items():
        if isinstance(answer, BaseException):
            raise answer
    return answers


def _bound_studio(hub: str, chat_id: str, timeout: float = 180) -> tuple[str, dict]:
    """The checks that say this tool may speak to this project's service at all.

    The checks themselves are unchanged and all of them still have to pass
    before anything is sent. What has changed is only the waiting: the four
    that ask different services about different things are asked at once, and
    the two that need the Studio's address are asked at once after it is known.
    Nothing here runs while its own precondition is still unanswered.
    """

    first = _together({
        "session": (hub, f"/api/chat/sessions/{_identifier(chat_id)}"),
        "configured": (hub, "/api/settings/apps"),
        "apps": (hub, "/api/apps"),
        "hub_health": (hub, "/api/health"),
    }, timeout)
    session = first["session"]
    if session.get("status") != "running":
        raise HubFailure(409, "CHAT_NOT_RUNNING", "This chat is no longer running.")
    if _project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
    configured = first["configured"]
    if not configured.get("projectDir") or str(Path(configured["projectDir"]).resolve()) != session["projectDir"]:
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "Open the tools for this conversation's project first.")
    studio = next((row for row in first["apps"] if row.get("appId") == "monkeyarch"), {})
    if studio.get("state") != "running" or not studio.get("url") or not studio.get("processId"):
        raise HubFailure(409, "CHAT_STUDIO_UNAVAILABLE", "Open MonkeyArch for this project before using a design tool.")
    base = _url(studio["url"])
    second = _together({
        "health": (base, "/api/health"),
        "binding": (base, "/api/project"),
    }, timeout)
    health = second["health"]
    if health.get("processId") != studio["processId"] or health.get("sourceRevision") != first["hub_health"].get("sourceRevision"):
        raise HubFailure(409, "CHAT_SERVICE_CHANGED", "The responding Studio is not the Hub's current service.")
    binding = second["binding"]
    if binding.get("projectId") != session["projectId"] or str(Path(binding.get("projectDir", "")).resolve()) != session["projectDir"]:
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The running application belongs to a different project.")
    return base, session


# The one action a caller may ask to see through in a single tool call, and the
# bounds it is held to. It is the capability route that already exists; nothing
# else is orchestrated, and nothing new executes anything.
_FINISHABLE = "/api/capabilities/candidate.modify_existing/run"
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
    reads = {"candidate": (base, f"/api/candidates/{candidate_id}"),
             "compare": (base, f"/api/candidates/{candidate_id}/compare?against={against}")}
    try:
        answers = _together(reads, left())
    except (HubFailure, OSError, TimeoutError) as cause:
        # The run finished; reading it back did not. Both facts travel, and
        # neither is allowed to stand in for the other.
        return {**started, "status": "succeeded", "readback": "failed",
                "detail": f"the run finished and could not be read back: {_reason(cause)}",
                "next": follow}
    candidate, comparison = answers["candidate"], answers["compare"]
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
        },
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
    base, session = _bound_studio(hub, chat_id)
    method, path = str(arguments.get("method", "GET")).upper(), arguments.get("path", "")
    parsed = urlsplit(path)
    allowed = {"GET": _READ, "POST": _POST, "PUT": _WRITE}
    if "awaitSeconds" in arguments and name != "studio_request":
        # Only one tool can wait for anything. Quietly dropping the option here
        # would answer at once and look like the wait had happened.
        raise HubFailure(422, "CHAT_TOOL_INVALID",
                         f"{name} has nothing to wait for; awaitSeconds is studio_request's option for "
                         f"POST {_FINISHABLE}.")
    if name == "fab_request":
        if method == "GET" and path == "/api/fab/profiles":
            return _request_json(hub, path)
        if method == "POST" and path == "/api/fab/send":
            body = dict(arguments.get("body") or {})
            body["dryRun"] = True
            body.pop("accessCode", None)
            return _request_json(hub, path, "POST", body)
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "The chat can list Fab profiles and validate a prepared job; uploads remain explicit in MonkeyFab.")
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
        return {"path": template, "method": method, "operation": operation, "components": {"schemas": schemas}}
    if name != "studio_request":
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "Unknown chat tool.")
    wait = arguments.get("awaitSeconds")
    if wait is not None:
        if not isinstance(wait, int) or isinstance(wait, bool) or not 1 <= wait <= _AWAIT_MAX_S:
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds is a whole number of seconds from 1 to {_AWAIT_MAX_S}.")
        if not (method == "POST" and parsed.path == _FINISHABLE):
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds is this tool's own option for POST {_FINISHABLE}, which is "
                             "the one action it can see through to its result. It is not a field of the "
                             "request body, and every other action answers as it is, without waiting.")
        if not isinstance(arguments.get("body"), dict) or not arguments["body"].get("sourceRunId"):
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
    started = _request_json(base, path, method, body)
    if wait is None:
        return started
    # One POST has happened. From here on this call only reads.
    return _finish(base, started, body or {}, time.monotonic() + wait)


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
    request_schema = {
        "type": "object", "properties": {
            **request_fields,
            "awaitSeconds": {
                "type": "integer", "minimum": 1, "maximum": _AWAIT_MAX_S,
                "description": "This tool's own option, beside method/path/body and never inside the "
                               "body; 60 suits an ordinary change. It is not an execution wrapper's "
                               "yield_time_ms: through functions.exec, omit yield_time_ms so it keeps its "
                               "30000 ms default, never set 1000, and never loop short functions.wait "
                               "calls. Only for POST " + _FINISHABLE + ", whose body must name "
                               "sourceRunId: wait this many seconds, counted from when that action is "
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
        "MAKE A MASSING OR ANY SOLID FORM (the usual first move; no schema lookup needed):",
        "1. GET /api/state -> stateDigest, components[].componentId, elements[].",
        "2. GET /api/state/frame -> levels[].levelId, for the base to stand on.",
        "3. POST /api/proposals/sketch with {stateDigest, componentId, elementId, profile: [[x,z], ...], height, baseLevel}.",
        "   Use baseDatum: '<elementId>-top' instead of baseLevel to stack on something already there.",
        "   elementId is yours to choose; sending the same elementId again changes that outline or height",
        "   instead of adding another form. Optional: summary, keep (refs this must not change), sourceRunId.",
        "4. POST /api/proposals/{id}/candidate -> {jobId, candidateId}. Poll GET /api/jobs/{id} until succeeded,",
        "   then GET /api/candidates/{id}. That candidate is the visible result: reversible, and published by nobody.",
        "Repeat 3-4 for each further form, stacking with baseDatum, passing the candidate run as sourceRunId to keep building on it.",
        "A new component needs parentComponentId (an existing component a seat builds) and semanticKind (what it is);",
        "drawing under a component no seat builds is refused with the list of the ones that are built.",
        "",
        "CHANGE SOMETHING THAT IS ALREADY THERE — two calls when you know what to change, three when you do not:",
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
        "/api/artifacts, /api/proposals/{id}, /api/jobs/{id}, /api/candidates/{id}.",
        "GET /api/candidates/{id}/compare?against=<runId> — the run to compare with is required, and it is the",
        "sourceRunId the candidate was made from (the run before it); without it the request is refused.",
        "OTHER EXISTING ACTIONS: POST /api/state/closure, /api/program, /api/options, /api/options/{id}/select,",
        "/api/candidates/combine, /api/drawings/elevations; PUT /api/board.",
        "No freeform intent/model call, direct semantic-operator route, issue, approvals or filesystem writes.",
    ])
    tools = [
        {"name": "studio_schema", "description": "Read the exact request/response schema of an allowed Studio action. "
         "The usual actions are already described in studio_request with their fields and units: use this only for an action "
         "that description does not cover, or a field it does not state. A path may be written with its template segments, "
         "such as /api/proposals/{id}/candidate.", "inputSchema": input_schema},
        {"name": "studio_request", "description": modelling, "inputSchema": request_schema},
        {"name": "fab_request", "description": "Use MonkeyFab GET /api/fab/profiles or POST /api/fab/send for dry-run validation only. This tool never uploads or starts printing.", "inputSchema": input_schema},
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
