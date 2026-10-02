"""The installed coding CLIs a conversation runs on, and the environment a turn gives them.

Where each CLI is, what a headless Claude turn may do without a prompt and
where it may not write, the Claude and Coding Plan configuration, the Codex
ACP adapter this application locks, each CLI's own sign-in console, and the
read-only checks of who a CLI is signed in as and which models it lists. ``_redact`` keeps provider credentials
out of every text the Hub keeps or shows.
"""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Mapping

from ..settings import credentials
from ..settings.store import read_user_settings

from ..models import HubFailure


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
    # reaches its routes through the chat's transport, which redacts with this
    # module, and one of the two has to be late for the other to exist.
    from .. import computer_tools

    if not computer_tools.read_policy(runtime_root).enabled:
        return _CLAUDE_APPROVED
    return _CLAUDE_APPROVED + tuple(
        f"mcp__monkeyhub__{name}" for name in computer_tools.TOOL_NAMES
    )


# The CLI reads a rule's path as a gitignore pattern. A folder name that
# carries one of these is matched literally only once they are escaped.
_RULE_PATTERN = re.compile(r"([\\*?\[\]])")


def _claude_denied(project_dir: str) -> tuple[str, ...]:
    """What a headless turn may not do although its tools are approved: write the bound project.

    The agent reads the project where it is, and its design changes go
    studio_request -> Hub -> runtime (ADR-012). One Edit rule denies every
    built-in tool that writes a file there, Write included. Measured with the
    installed CLI 2.1.283 (#599): a Write rule gates no path at all, an
    absolute Windows path is matched as //<drive letter>/<path>, and
    //D:/<path> silently matches nothing. Bash is not a file tool, so this
    does not confine it.
    """
    text = str(project_dir)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    drive = re.match(r"([A-Za-z]):(?:[\\/]|$)", text)
    if drive:
        root, path = "//" + drive.group(1).lower(), text[2:].replace("\\", "/")
    elif text.startswith(("\\\\", "//")):
        root, path = "//", text[2:].replace("\\", "/")
    else:
        root, path = "/", text
    escaped = _RULE_PATTERN.sub(r"\\\1", path).rstrip("/")
    return (f"Edit({root}{escaped}/**)",)


def _source_checkout() -> Path | None:
    """This Hub's own source tree, when it is one somebody can work in.

    A development checkout answers with its root: the assistant reads and
    changes the code it is running, runs its commands there, and fixes what it
    breaks. An installed bundle is not one — there is no repository and nothing
    to edit — and this answers None so the caller can say so rather than
    pretend the code is writable.
    """
    root = Path(__file__).resolve().parents[5]
    if not (root / ".git").exists() or not (root / "AGENTS.md").is_file():
        return None
    return root if os.access(root, os.W_OK) else None


def _claude_user_settings() -> dict:
    """The user's own Claude Code ``settings.json`` (under ``CLAUDE_CONFIG_DIR`` when set), or {}."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        saved = json.loads((root / "settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _claude_env() -> dict[str, str]:
    values = dict(os.environ)
    saved = _claude_user_settings()
    env = saved.get("env")
    for key, value in (env if isinstance(env, dict) else {}).items():
        if isinstance(key, str) and isinstance(value, str) and key not in values:
            values[key] = value
    return values


def _coding_plan_env() -> dict[str, str]:
    """The Coding Plan endpoint and token saved in Hub settings (#334), when both are.

    They reach Coding Plan conversations only; a plain Claude Code conversation
    keeps the CLI's own login and configuration."""
    token = credentials.saved("coding-plan")
    try:
        base = read_user_settings().coding_plan_base_url
    except Exception:  # noqa: BLE001 - unreadable preferences mean no saved endpoint
        base = None
    return {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": token} if token and base else {}


def claude_plan_configured(environment: Mapping[str, str] | None = None) -> bool:
    """Whether the Claude CLI's own configuration names an Anthropic-compatible endpoint and its credential."""
    env = _claude_env() if environment is None else environment
    return bool(env.get("ANTHROPIC_BASE_URL") and (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")))


# #334: each CLI's own sign-in; it runs in a console of its own and may open the browser.
_LOGIN_ARGS = {"codex": ("login",), "claude": ("auth", "login")}


def _console_available() -> bool:
    """A sign-in window is a Windows console; elsewhere the person runs the CLI's login themselves."""
    return os.name == "nt"


def _open_console(command_line: str, cwd: str) -> None:
    """Run a command in a new console window. `start` gives it that window's own
    keyboard; the short-lived starter reads nothing, so the Hub's stdin stays the Hub's."""
    subprocess.Popen(f'cmd.exe /d /c start "MonkeyHub sign-in" {command_line}', cwd=cwd,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _redact(text: str, environment: Mapping[str, str] | None = None) -> str:
    """Never expose provider credentials in the transcript or an error body."""
    for key, value in (environment or os.environ).items():
        if re.search(r"key|token|secret|password|access.?code", key, re.I) and len(value) >= 8:
            text = text.replace(value, "[redacted]")
    # Keys saved in Hub settings are never in the environment of this process.
    for value in credentials.saved_values():
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
    hub = Path(__file__).resolve().parents[3]
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
