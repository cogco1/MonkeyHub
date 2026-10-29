"""What a person allowed a conversation's Codex agent to do in one project (A4), kept by the Hub in ``tool-grants.json``.

Codex asks before each command it runs outside its sandbox and each time it goes online. Claude, in the same
Hub, is started with commands and the network already allowed. So a person answers once, for a project: the card's
"always allow in this project" writes one grant here, and every later request of that kind in that project is
answered by the Hub from it, with a line in the conversation saying so. Settings › What the AI may do lists the
grants beside the skills' (``skill_grants``), with the same Withdraw; a withdrawn grant stays in the file with
when it was withdrawn and allows nothing.

Three kinds of request can be allowed this way, and only these:

- ``run``: a command the agent starts. Claude's Bash is not confined either.
- ``network``: the agent goes online.
- ``hub``: a call to MonkeyHub's own tools, which the Hub gates and attributes; Claude has them without asking.

A tool of any other MCP server is never one of them.

- ``edit``: a file tool, only in a plain folder a conversation works in (projectId ``folder:...``), which it writes.

An edit is never one of them. The bound project is read-only to every agent's file tools (ADR-012), and Claude
is denied every edit there; a project-wide grant of edits would give Codex more than Claude has. Nothing here is
read by a Project Runtime, and no grant here reaches a skill (``skill_grants`` is a separate file).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import HubFailure
from . import store

GRANTS_FILE = Path("config") / "tool-grants.json"
GRANTS_SCHEMA = "MonkeyHubToolGrants@1"
ACTIONS = ("run", "network", "hub", "edit")
_lock = threading.Lock()


class ToolGrantDto(BaseModel):
    """One answer a person gave: ``action`` is allowed in ``projectId`` until it is withdrawn."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    grant_id: str = Field(alias="grantId")
    project_id: str = Field(alias="projectId", min_length=1)
    action: Literal["run", "network", "hub", "edit"]
    granted_at: str = Field(alias="grantedAt")
    # The permission card the person answered.
    ref: str | None = None
    # A folder grant's folder, as Settings shows it; a project's id names its project already.
    place: str | None = None
    revoked_at: str | None = Field(default=None, alias="revokedAt")
    revoked_by: str | None = Field(default=None, alias="revokedBy")


class ToolGrantsDto(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["MonkeyHubToolGrants@1"] = Field(default=GRANTS_SCHEMA, alias="schema")
    grants: list[ToolGrantDto] = Field(default_factory=list)


def grants_path(runtime_root: Path) -> Path:
    """Where this Hub keeps the grants, beside ``applications.json`` and ``skill-grants.json``."""

    if not runtime_root.is_absolute():
        raise store.SettingsError("The application runtime root must be absolute.")
    return runtime_root / GRANTS_FILE


def read(runtime_root: Path) -> ToolGrantsDto:
    """Every grant, withdrawn ones included; none before the first answer, a refusal when the file is damaged."""

    path = grants_path(runtime_root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ToolGrantsDto()
    except OSError as failure:
        raise _unreadable(f"could not be read ({failure})") from failure
    try:
        return ToolGrantsDto.model_validate_json(text)
    except ValidationError as failure:
        raise _unreadable("is damaged") from failure


def covers(runtime_root: Path, project_id: str, action: str) -> bool:
    """Whether a grant in force allows ``action`` in this project. A file that cannot be read allows nothing."""

    try:
        document = read(runtime_root)
    except HubFailure:
        return False
    return any(row.project_id == project_id and row.action == action and row.revoked_at is None
               for row in document.grants)


def allow(runtime_root: Path, project_id: str, action: str, *, ref: str | None = None, place: str | None = None,
          now: datetime | None = None) -> ToolGrantDto:
    """Keep the answer: ``action`` is allowed in this project. Allowing what is already allowed answers the grant in force."""

    if action not in ACTIONS:
        raise HubFailure(422, "TOOL_GRANT_INVALID", f"Only {', '.join(ACTIONS)} can be allowed for a whole project.")
    if action == "edit" and not project_id.startswith("folder:"):
        # A project is read-only to every agent's file tools (ADR-012); only a plain folder is written.
        raise HubFailure(422, "TOOL_GRANT_INVALID", "Edits can be allowed for a folder, never for a project.")
    with _lock:
        document = read(runtime_root)
        held = next((row for row in document.grants
                     if row.project_id == project_id and row.action == action and row.revoked_at is None), None)
        if held is not None:
            return held
        row = ToolGrantDto(grantId=uuid.uuid4().hex, projectId=project_id, action=action, ref=ref, place=place,
                           grantedAt=(now or datetime.now(timezone.utc)).isoformat())
        document.grants.append(row)
        store.save_tool_grants(grants_path(runtime_root), document)
    return row


def revoke(runtime_root: Path, grant_id: str, *, by: str, now: datetime | None = None) -> ToolGrantDto:
    """Withdraw one grant; its record stays, and no later request is allowed by it. Withdrawing twice answers the first."""

    with _lock:
        document = read(runtime_root)
        row = next((item for item in document.grants if item.grant_id == grant_id), None)
        if row is None:
            raise HubFailure(404, "TOOL_GRANT_NOT_FOUND", f"No tool grant {grant_id}.")
        if row.revoked_at is None:
            row.revoked_at, row.revoked_by = (now or datetime.now(timezone.utc)).isoformat(), by
            store.save_tool_grants(grants_path(runtime_root), document)
    return row


def _unreadable(why: str) -> HubFailure:
    return HubFailure(409, "TOOL_GRANTS_UNREADABLE",
                      f"The tool grants file {why}, so nothing is taken as allowed. It is kept as it is.")
