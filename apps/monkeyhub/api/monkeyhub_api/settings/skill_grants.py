"""What a person allowed skills to do on this machine (S-1), kept by the Hub in ``skill-grants.json``.

A person allows kinds of access, not a skill's code: an item allowed once holds
for every skill, version and project until it is withdrawn. The file lives in
the Hub's own settings folder, beside ``applications.json``, outside every
project and never shared with a team. Only the Hub writes it, when a person
answers a card or changes Settings; a Project Runtime reads it at the path its
launch environment names, to check a run before it starts. Its form is the
Runtime's wire shape (``project_runtime.api.dto.skill_grants``), each item as
the Runtime compares it.

Each answer is its own grant, so Settings can say what was allowed when, and
withdrawing one leaves the others. A withdrawn grant stays in the file with
when it was withdrawn and allows nothing. Three kinds of answer:

- allowed for every skill: an item that allows anything (a shell or another
  interpreter, ``*``, a package from an address) is refused here, and the
  Runtime ignores one that reaches the file another way;
- allowed for one skill, by name and source, which may hold such an item;
- allowed for one run: bound to the project, skill and code the card showed,
  for ``ONCE_MINUTES``, and taken by the first run that names it.

A version grant a project still holds from before S-1 is carried over once,
as a grant for that skill only (``migrate``): the person allowed that skill,
not every skill.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from project_runtime.api.dto.skill_grants import SkillAccessDto, SkillGrantDto, SkillGrantsDto, SkillRefDto

from ..models import HubFailure
from . import store

GRANTS_FILE = Path("config") / "skill-grants.json"
ONCE_MINUTES = 30
_lock = threading.Lock()


class SkillGrantCarryRequest(BaseModel):
    """A project's approvals from before S-1, as its Runtime lists them (GET /api/skills/approvals)."""

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId", min_length=1)
    approvals: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


def grants_path(runtime_root: Path) -> Path:
    """Where this Hub keeps the grants: the path a Project Runtime it starts is launched with."""

    if not runtime_root.is_absolute():
        raise store.SettingsError("The application runtime root must be absolute.")
    return runtime_root / GRANTS_FILE


def read(runtime_root: Path) -> SkillGrantsDto:
    """Every grant, withdrawn ones included; none before the first answer, a refusal when the file is damaged."""

    path = grants_path(runtime_root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return SkillGrantsDto()
    except OSError as failure:
        raise _unreadable(f"could not be read ({failure})") from failure
    try:
        return SkillGrantsDto.model_validate_json(text)
    except ValidationError as failure:
        raise _unreadable("is damaged") from failure


def allow(runtime_root: Path, access: Mapping[str, Any], *, method: Literal["card", "settings"],
          skill: SkillRefDto | None = None, once: Mapping[str, str] | None = None,
          ref: str | None = None, now: datetime | None = None) -> SkillGrantDto:
    """Keep one answer: ``access`` for every skill, for ``skill`` only, or for one run of it (``once``).

    ``once`` names the run's ``projectId`` and ``codeSha256``; it needs ``skill``.
    Allowing nothing, or something that allows anything for every skill, is refused.
    """

    form = SkillAccessDto.model_validate(dict(access))
    if form.empty():
        raise HubFailure(422, "SKILL_GRANT_EMPTY", "A grant allows at least one program, host, folder, package or the network.")
    if once is not None and skill is None:
        raise HubFailure(422, "SKILL_GRANT_INVALID", "A grant for one run names the skill the card showed.")
    wide = form.broad()
    if wide and skill is None:
        raise HubFailure(422, "SKILL_GRANT_TOO_BROAD",
                         "These allow anything and are allowed only for one skill or one run: "
                         + "; ".join(f"{key} {', '.join(items)}" for key, items in wide.items()) + ".")
    moment = now or datetime.now(timezone.utc)
    bound = None
    if once is not None:
        if set(once) != {"projectId", "codeSha256"}:
            raise HubFailure(422, "SKILL_GRANT_INVALID", "A grant for one run names its projectId and codeSha256.")
        bound = {**once, "expiresAt": (moment + timedelta(minutes=ONCE_MINUTES)).isoformat()}
    row = SkillGrantDto(grantId=uuid.uuid4().hex, access=form, skill=skill, once=bound, method=method, ref=ref,
                        grantedAt=moment.isoformat())
    with _lock:
        document = read(runtime_root)
        document.grants.append(row)
        _save(runtime_root, document)
    return row


def revoke(runtime_root: Path, grant_id: str, *, by: str, now: datetime | None = None) -> SkillGrantDto:
    """Withdraw one grant; its record stays, and no later run is allowed by it. Withdrawing twice answers the first."""

    with _lock:
        document = read(runtime_root)
        row = next((item for item in document.grants if item.grant_id == grant_id), None)
        if row is None:
            raise HubFailure(404, "SKILL_GRANT_NOT_FOUND", f"No skill grant {grant_id}.")
        if row.revoked_at is None:
            row.revoked_at, row.revoked_by = (now or datetime.now(timezone.utc)).isoformat(), by
            _save(runtime_root, document)
    return row


def migrate(runtime_root: Path, project_id: str, approvals: Iterable[Mapping[str, Any]], *,
            source_of: Callable[[str], str | None], now: datetime | None = None) -> list[SkillGrantDto]:
    """Carry a project's version grants from before S-1 over once each, as grants for that skill only.

    ``approvals`` are the project's StudioSkillApproval@1 rows still in force.
    A once grant allowed one run and is not carried. ``source_of(name)`` is
    where that skill is now found ("bundled" or "user"), or None: a grant is not
    carried onto a skill from a shared folder, or onto one no folder holds. A
    grant carried before is never carried again, also after it was withdrawn.
    """

    moment = (now or datetime.now(timezone.utc)).isoformat()
    added = []
    with _lock:
        document = read(runtime_root)
        carried = {f"{row.migrated_from.get('projectId')}/{row.migrated_from.get('approvalId')}"
                   for row in document.grants if row.method == "migrated" and row.migrated_from}
        for approval in approvals:
            name = str(approval.get("skillId", "")).removeprefix("skill:")
            key = f"{project_id}/{approval.get('approvalId')}"
            if approval.get("scope", "version") != "version" or key in carried:
                continue
            form = SkillAccessDto.model_validate(dict(approval.get("access") or {}))
            source = source_of(name)
            if form.empty() or source not in ("bundled", "user"):
                continue
            added.append(SkillGrantDto(
                grantId=uuid.uuid4().hex, access=form, method="migrated", skill=SkillRefDto(name=name, source=source),
                grantedAt=moment, ref=str(approval.get("approvalId")),
                migratedFrom={"projectId": project_id, "approvalId": approval.get("approvalId"),
                              "version": approval.get("version"), "codeSha256": approval.get("codeSha256"),
                              "approvedAt": approval.get("approvedAt"), "approvedBy": approval.get("approvedBy")}))
            carried.add(key)
        if added:
            document.grants.extend(added)
            _save(runtime_root, document)
    return added


def _save(runtime_root: Path, document: SkillGrantsDto) -> None:
    store.save_skill_grants(grants_path(runtime_root), document)


def _unreadable(why: str) -> HubFailure:
    return HubFailure(409, "SKILL_GRANTS_UNREADABLE",
                      f"The skill grants file {why}, so nothing is taken as allowed. It is kept as it is.")
