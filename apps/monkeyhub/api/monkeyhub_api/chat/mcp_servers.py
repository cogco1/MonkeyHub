"""The other MCP servers a Hub conversation may use beside MonkeyHub's own: Drive, Blender, Rhino, a browser.

Two sources, one switch each, kept in ``<runtime>/config/mcp-servers.json``:

- ``fromClaude``: the servers the person already set up in Claude Code (its user configuration,
  ``~/.claude.json`` or ``$CLAUDE_CONFIG_DIR/.claude.json``, key ``mcpServers``), and for the conversation's own
  folder the servers Claude Code keeps for that folder (``projects.<folder>.mcpServers`` there, and the folder's
  ``.mcp.json`` servers the person approved in Claude Code), read again at each turn;
- ``servers``: servers registered in the Hub itself, in the same shape Claude Code writes.

``disabled`` names servers of either source the person turned off. ``monkeyhub`` is the Hub's own and can never be
replaced. What a turn is given is ``effective``; a server's environment and headers often hold keys, so they are
never shown (``public``), never put on a command line (the Claude CLI reads them from a file under the Hub's
guarded ``config`` folder, Codex from its environment), and every such value is redacted from what the Hub keeps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import HubFailure

CONFIG_FILE = Path("config") / "mcp-servers.json"
SCHEMA = "MonkeyHubMcpServers@1"
RESERVED = "monkeyhub"
# A server's name becomes part of every tool name (mcp__<server>__<tool>) and of a permission rule.
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_lock = threading.Lock()


class McpServersDto(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["MonkeyHubMcpServers@1"] = Field(default=SCHEMA, alias="schema")
    fromClaude: bool = False
    servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    disabled: list[str] = Field(default_factory=list)


class McpServerView(BaseModel):
    """One server as the settings page lists it: never its environment's or headers' values."""

    name: str
    source: Literal["hub", "claude"]
    transport: Literal["stdio", "http", "sse"]
    target: str = Field(description="the command it runs, or the URL it reaches")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    envKeys: list[str] = Field(default_factory=list)
    enabled: bool


class McpServersView(BaseModel):
    fromClaude: bool
    servers: list[McpServerView]
    # Why Claude Code's servers could not be read, when they could not.
    claudeProblem: str | None = None


class McpServersUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    fromClaude: bool | None = None
    disabled: list[str] | None = Field(default=None, max_length=200)
    # Hub-registered servers, replaced whole when given. An entry whose env or headers value is "[kept]" keeps the
    # saved value, so the page can send back what it was shown without ever having seen a key.
    servers: dict[str, dict[str, Any]] | None = None
    # One server added to the Hub's list (or replaced, by name), and Hub servers taken off it.
    add: dict[str, dict[str, Any]] | None = Field(default=None, max_length=20)
    remove: list[str] | None = Field(default=None, max_length=200)


def config_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / CONFIG_FILE


def read(runtime_root: Path) -> McpServersDto:
    path = config_path(runtime_root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return McpServersDto()
    except OSError as failure:
        raise HubFailure(409, "CHAT_MCP_CONFIG_UNREADABLE", f"The MCP server list could not be read ({failure}).") from failure
    try:
        return McpServersDto.model_validate_json(text)
    except ValidationError as failure:
        raise HubFailure(409, "CHAT_MCP_CONFIG_UNREADABLE", "The MCP server list is damaged; it is kept as it is.") from failure


def _save(runtime_root: Path, document: McpServersDto) -> None:
    from ..settings.store import _save_local_settings

    _save_local_settings(config_path(runtime_root), document)


def claude_config_path() -> Path:
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(root) / ".claude.json" if root else Path.home() / ".claude.json"


def claude_servers() -> tuple[dict[str, dict[str, Any]], str | None]:
    """The servers in Claude Code's user configuration, and why they could not be read when they could not."""
    path = claude_config_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as failure:
        return {}, f"{path.name} could not be read ({type(failure).__name__})."
    servers = saved.get("mcpServers") if isinstance(saved, dict) else None
    return ({name: dict(row) for name, row in servers.items() if isinstance(row, dict)}
            if isinstance(servers, dict) else {}), None


def _folder_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", os.sep))).rstrip("\\/")


def claude_folder_servers(folder: str | None) -> dict[str, dict[str, Any]]:
    """The servers Claude Code keeps for this folder: its own entry in the user configuration, then the folder's
    ``.mcp.json`` servers the person approved there (``enabledMcpjsonServers`` or ``enableAllProjectMcpServers``)."""
    if not folder:
        return {}
    try:
        saved = json.loads(claude_config_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        saved = {}
    projects = saved.get("projects") if isinstance(saved, dict) else None
    key = _folder_key(folder)
    entry = next((row for name, row in (projects or {}).items()
                  if isinstance(row, dict) and _folder_key(str(name)) == key), {}) if isinstance(projects, dict) else {}
    found: dict[str, dict[str, Any]] = {}
    try:
        shared = json.loads((Path(folder) / ".mcp.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        shared = {}
    servers = shared.get("mcpServers") if isinstance(shared, dict) else None
    approved = set(entry.get("enabledMcpjsonServers") or [])
    refused = set(entry.get("disabledMcpjsonServers") or [])
    for name, row in (servers.items() if isinstance(servers, dict) else ()):
        if isinstance(row, dict) and name not in refused and (entry.get("enableAllProjectMcpServers") or name in approved):
            found[name] = dict(row)
    own = entry.get("mcpServers")
    found.update({name: dict(row) for name, row in (own.items() if isinstance(own, dict) else ()) if isinstance(row, dict)})
    return found


def _transport(row: dict[str, Any]) -> str | None:
    kind = row.get("type") or ("stdio" if row.get("command") else "http" if row.get("url") else None)
    if kind == "stdio" and isinstance(row.get("command"), str) and row["command"]:
        return "stdio"
    if kind in ("http", "sse") and isinstance(row.get("url"), str) and re.match(r"^https?://", row["url"]):
        return kind
    return None


def _usable(name: str, row: dict[str, Any]) -> bool:
    return name != RESERVED and NAME.fullmatch(name) is not None and _transport(row) is not None


def effective(runtime_root: Path, folder: str | None = None) -> dict[str, dict[str, Any]]:
    """What a turn in ``folder`` is given, by name: Claude Code's servers when switched on (the user's, then that
    folder's own, as Claude Code itself ranks them), then the Hub's, minus the disabled.

    A configuration that cannot be read gives nothing beyond MonkeyHub's own: the turn still runs.
    """
    try:
        document = read(runtime_root)
    except HubFailure:
        return {}
    found: dict[str, dict[str, Any]] = {}
    if document.fromClaude:
        found.update(claude_servers()[0])
        found.update(claude_folder_servers(folder))
    found.update(document.servers)
    return {name: _clean(row) for name, row in found.items()
            if name not in document.disabled and _usable(name, row)}


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """A server as the Claude CLI's --mcp-config reads it: its transport and what that transport needs."""
    kind = _transport(row)
    if kind == "stdio":
        value = {"type": "stdio", "command": row["command"],
                 "args": [str(item) for item in row.get("args") or [] if isinstance(item, (str, int, float))]}
        env = {str(key): str(item) for key, item in (row.get("env") or {}).items() if isinstance(item, (str, int, float))}
        if env:
            value["env"] = env
        return value
    value = {"type": kind, "url": row["url"]}
    headers = {str(key): str(item) for key, item in (row.get("headers") or {}).items() if isinstance(item, (str, int, float))}
    if headers:
        value["headers"] = headers
    return value


def secrets(servers: dict[str, dict[str, Any]]) -> list[str]:
    """Every environment and header value of these servers: what the Hub redacts wherever it keeps text."""
    values = []
    for row in servers.values():
        for part in ("env", "headers"):
            values += [item for item in (row.get(part) or {}).values() if isinstance(item, str) and len(item) >= 8]
    return values


def public(runtime_root: Path) -> McpServersView:
    from .providers import _redact

    document = read(runtime_root)
    claude, problem = claude_servers()
    rows = []
    for source, servers in (("claude", claude), ("hub", document.servers)):
        for name, row in sorted(servers.items()):
            if not _usable(name, row):
                continue
            clean = _clean(row)
            stdio = clean["type"] == "stdio"
            # An argument can carry a key as well (--api-key ...): what the page reads is redacted like a transcript.
            args = [_redact(item) for item in clean.get("args", [])]
            rows.append(McpServerView(
                name=name, source=source, transport=clean["type"],
                target=_redact(" ".join([clean["command"], *clean["args"]]) if stdio else clean["url"]),
                command=clean["command"] if stdio else None, args=args, url=None if stdio else _redact(clean["url"]),
                envKeys=sorted([*clean.get("env", {}), *clean.get("headers", {})]),
                enabled=name not in document.disabled and (source == "hub" or document.fromClaude)))
    return McpServersView(fromClaude=document.fromClaude, servers=rows, claudeProblem=problem)


def update(runtime_root: Path, change: McpServersUpdate) -> McpServersView:
    with _lock:
        document = read(runtime_root)
        if change.fromClaude is not None:
            document.fromClaude = change.fromClaude
        if change.disabled is not None:
            document.disabled = sorted(set(change.disabled))
        if change.servers is not None:
            servers = {}
            for name, row in change.servers.items():
                if not _usable(name, row):
                    raise HubFailure(422, "CHAT_MCP_SERVER_INVALID",
                                     f"{name[:64]!r} is not a usable server: a name of letters, digits, - and _ other than "
                                     "monkeyhub, with a command (stdio) or an http(s) URL.")
                row = dict(row)
                kept = document.servers.get(name, {})
                for part in ("env", "headers"):
                    if isinstance(row.get(part), dict):
                        row[part] = {key: (kept.get(part, {}).get(key) if value == "[kept]" else value)
                                     for key, value in row[part].items()}
                        row[part] = {key: value for key, value in row[part].items() if value is not None}
                servers[name] = row
            document.servers = servers
        for name, row in (change.add or {}).items():
            if not _usable(name, row):
                raise HubFailure(422, "CHAT_MCP_SERVER_INVALID",
                                 f"{name[:64]!r} is not a usable server: a name of letters, digits, - and _ other than "
                                 "monkeyhub, with a command (stdio) or an http(s) URL.")
            document.servers[name] = dict(row)
        for name in change.remove or ():
            document.servers.pop(name, None)
            document.disabled = [item for item in document.disabled if item != name]
        _save(runtime_root, document)
    return public(runtime_root)


def write_turn_config(runtime_root: Path, chat_id: str, servers: dict[str, dict[str, Any]]) -> Path:
    """The file a Claude turn reads these servers from, under the Hub's guarded config folder, replaced each turn."""
    from ..settings.store import _save_local_settings

    class _Document(BaseModel):
        mcpServers: dict[str, dict[str, Any]]

    path = Path(runtime_root) / "config" / "chat-mcp" / f"{chat_id}.json"
    _save_local_settings(path, _Document(mcpServers=servers))
    return path
