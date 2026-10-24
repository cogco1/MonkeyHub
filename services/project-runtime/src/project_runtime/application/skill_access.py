"""What a person allowed skills to do, and what a run asks for beyond it (S-1).

A person allows what skills may do on this machine, not a skill's code. A
runnable skill declares its ``access``: the programs it starts, whether it uses
the network, the hosts it reaches by ssh, the folders outside its run it writes,
the Python packages it installs. Each of those is allowed once and holds for
every skill, every version and every project, until it is withdrawn in Settings.
A run is asked about only what nothing allowed yet, and a new version only
about what it asks for beyond its earlier ones.

Reading is not asked about: the runner does not hold a script to what it reads,
so a grant of reading would promise what nothing keeps.

Some access is the same as allowing anything, because what it starts is not
held by the runner's audit hook (a shell or another interpreter runs whatever
it is given) or because it names no place at all (``*``, a package from an
address rather than the index). Such an item is never allowed for every skill:
only for one run, or for one skill by name and source.

The allowed access lives in the Hub's ``skill-grants.json`` (``GRANTS_SCHEMA``),
written only by the Hub when a person answers a card or changes Settings. A
Project Runtime reads it at the path its launch environment names
(``GRANTS_ENV``) and never writes it. The file is outside every project and is
not shared with a team.

Per key, ``access_added`` compares:

- ``network``: asking for it needs a grant that allowed it;
- ``programs``: by program name, without case or ``.exe``;
- ``ssh``: by host as written;
- ``packages``: by distribution name (``numpy>=2`` and ``NumPy==2.1`` are both
  numpy); a requirement naming an address is compared as written;
- ``write``: each folder lies within an allowed folder, the folder itself or
  below it, or the grant allowed ``*``. A folder named with a ``..`` step is
  never within anything: ``D:\\proj\\out\\..\\..\\Windows`` reads below
  ``out`` and is not. The runner holds a script to its folders after following
  links (``realpath``), so a caller on the machine that runs the skill passes
  the same ``resolve`` and a link out of an allowed folder is not within it either.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import ntpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
from typing import Any

ASKED = ("programs", "ssh", "write", "packages")
ANYWHERE = "*"
GRANTS_ENV = "MONKEYHUB_SKILL_GRANTS"
GRANTS_SCHEMA = "MonkeyHubSkillGrants@1"
SOURCES = ("bundled", "user", "shared")
# Programs that run whatever they are given. The audit hook sees only that one
# of them started, never what it then does, so allowing one allows anything.
# "shell" is how the runner names os.system.
INTERPRETERS = frozenset({
    "shell", "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "wsl",
    "python", "python3", "pythonw", "py", "pyw", "node", "deno", "bun", "ruby", "perl", "php", "lua", "java",
    "wscript", "cscript", "mshta", "rundll32", "regsvr32", "msiexec", "conhost", "start", "env", "sudo", "runas",
})

# The same interpreters under a version number: python3.12, node20, pwsh7.
_VERSIONED = re.compile(r"^(python|pythonw|py|node|ruby|perl|php|lua|java|pwsh|bash)[0-9][0-9._-]*$")
_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_WORDS = {"programs": "run", "ssh": "connect by ssh to", "write": "write to", "packages": "install"}
_WORDS_ZH = {"programs": "运行", "ssh": "用 ssh 连接", "write": "写入", "packages": "安装"}


Resolve = Callable[[str], str]


# ---- one declaration, in the form a person allows ----------------------------


def program_name(item: str) -> str:
    """A program as the runner names it: the file name, lower case, without ``.exe``."""

    return ntpath.basename(posixpath.basename(item.strip())).lower().removesuffix(".exe")


def package_name(item: str) -> str:
    """A requirement's distribution name (PEP 503), or the requirement itself when it names an address."""

    if "@" in item or "://" in item:
        return item.strip()
    found = _REQUIREMENT_NAME.match(item)
    return re.sub(r"[-_.]+", "-", found.group(1)).lower() if found else item.strip()


def asked(access: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a declaration asks a person for: network and the four lists, each item in its compared form."""

    access = access or {}
    form: dict[str, Any] = {"network": bool(access.get("network"))}
    for key in ASKED:
        items = [str(item) for item in access.get(key) or ()]
        if key == "programs":
            items = [ANYWHERE if item == ANYWHERE else program_name(item) for item in items]
        elif key == "packages":
            items = [package_name(item) for item in items]
        form[key] = sorted(set(items), key=items.index)
    return form


def empty(access: Mapping[str, Any]) -> bool:
    return not access.get("network") and not any(access.get(key) for key in ASKED)


def broad(access: Mapping[str, Any]) -> dict[str, list[str]]:
    """The items of a declaration that allow anything, which are never allowed for every skill."""

    form = asked(access)
    found = {
        "programs": [item for item in form["programs"]
                     if item == ANYWHERE or item in INTERPRETERS or _VERSIONED.fullmatch(item)],
        "ssh": [item for item in form["ssh"] if item == ANYWHERE],
        "write": [item for item in form["write"] if item == ANYWHERE],
        "packages": [item for item in form["packages"] if "@" in item or "://" in item],
    }
    return {key: items for key, items in found.items() if items}


def access_added(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None, *,
                 resolve: Resolve | None) -> dict[str, Any]:
    """What ``requested`` asks for beyond ``granted``: ``{}`` when it stays within it.

    ``resolve`` turns a declared folder into the folder the runner will hold
    the script to (``os.path.realpath`` where the skill runs). It is always
    named: None compares the folders as written, ``.`` steps removed, which
    only describes a declaration; deciding goes through ``access_within``.
    """

    requested, granted = asked(requested), asked(granted)
    added: dict[str, Any] = {}
    if requested["network"] and not granted["network"]:
        added["network"] = True
    for key in ASKED:
        new = [item for item in requested[key] if not _covered(key, item, granted[key], resolve)]
        if new:
            added[key] = new
    return added


def access_within(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None, *,
                  resolve: Resolve) -> bool:
    """Whether a run declaring ``requested`` may go ahead on what ``granted`` allows, without asking.

    Deciding not to ask follows links as the runner will: ``resolve`` is
    required, so a link out of an allowed folder cannot be let through by
    a caller that forgot it.
    """

    if not callable(resolve):
        raise TypeError("access_within decides whether to ask again, so it resolves folders as the runner does")
    return not access_added(requested, granted, resolve=resolve)


def merged(accesses: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Everything several grants allow together."""

    total: dict[str, Any] = {"network": False, **{key: [] for key in ASKED}}
    for access in accesses:
        form = asked(access)
        total["network"] = total["network"] or form["network"]
        for key in ASKED:
            total[key] += [item for item in form[key] if item not in total[key]]
    return total


def describe_added(added: Mapping[str, Any]) -> str:
    """What is asked for, in one line for the card: "use the network; run blender; connect by ssh to renou-win"."""

    parts = ["use the network"] if added.get("network") else []
    parts += [f"{_WORDS[key]} {', '.join(added[key])}" for key in ASKED if added.get(key)]
    return "; ".join(parts)


def describe_added_zh(added: Mapping[str, Any]) -> str:
    """The same line in Chinese: 「联网；运行 blender；用 ssh 连接 renou-win」."""

    parts = ["联网"] if added.get("network") else []
    parts += [f"{_WORDS_ZH[key]} {'、'.join(added[key])}" for key in ASKED if added.get(key)]
    return "；".join(parts)


def _covered(key: str, item: str, allowed: list[str], resolve: Resolve | None) -> bool:
    if key != "write":
        return item in allowed or (key in ("programs", "ssh") and ANYWHERE in allowed)
    if ANYWHERE in allowed:
        return True
    folder = _folder(item, resolve)
    if folder is None:
        return False
    return any(base is not None and type(folder) is type(base) and (folder == base or base in folder.parents)
               for base in (_folder(row, resolve) for row in allowed if row != ANYWHERE))


def _folder(text: str, resolve: Resolve | None) -> PurePosixPath | PureWindowsPath | None:
    """The folder a declaration names, or None when no folder can stand for it (``*``, a ``..`` step)."""

    windows = bool(_WINDOWS.match(text))
    if text == ANYWHERE or ".." in re.split(r"[\\/]" if windows else "/", text):
        return None
    if resolve is not None:
        text = resolve(text)
        windows = bool(_WINDOWS.match(text))
    # A drive or UNC path is compared as Windows compares it, without regard to case or slash direction.
    return PureWindowsPath(ntpath.normpath(text)) if windows else PurePosixPath(posixpath.normpath(text))


# ---- the grants a person gave ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillRef:
    """A skill as a grant names it: its name and the folder it came from."""

    name: str
    source: str


@dataclass(frozen=True, slots=True)
class Grant:
    """One answer a person gave: what it allows, and for which skills and runs.

    ``skill`` None allows every skill. ``once`` binds a grant to exactly one run:
    the project, skill and code a card showed, until ``expires_at``.
    """

    grant_id: str
    access: Mapping[str, Any]
    skill: SkillRef | None
    once: Mapping[str, str] | None
    granted_at: str
    method: str
    revoked_at: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def applies_to(self, skill: SkillRef) -> bool:
        return self.skill is None or self.skill == skill


class GrantsUnreadable(ValueError):
    """The grants file is there and cannot be read; nothing is taken as allowed."""


def parse_grants(text: str) -> tuple[Grant, ...]:
    """The grants a ``skill-grants.json`` holds, or GrantsUnreadable: a damaged file allows nothing."""

    try:
        document = json.loads(text)
    except ValueError as failure:
        raise GrantsUnreadable("skill-grants.json is not JSON.") from failure
    if not isinstance(document, dict) or document.get("schema") != GRANTS_SCHEMA or not isinstance(
            document.get("grants"), list):
        raise GrantsUnreadable(f"skill-grants.json is not {GRANTS_SCHEMA}.")
    rows = []
    for row in document["grants"]:
        try:
            skill = row.get("skill")
            rows.append(Grant(
                grant_id=str(row["grantId"]), access=asked(row["access"]),
                skill=None if skill is None else SkillRef(str(skill["name"]), str(skill["source"])),
                once=None if row.get("once") is None else {key: str(value) for key, value in row["once"].items()},
                granted_at=str(row["grantedAt"]), method=str(row["method"]),
                revoked_at=None if row.get("revokedAt") is None else str(row["revokedAt"])))
        except (AttributeError, KeyError, TypeError) as failure:
            raise GrantsUnreadable("skill-grants.json holds a grant without its id, access or time.") from failure
    return tuple(rows)


def read_grants(path: Path | None) -> tuple[Grant, ...]:
    """The grants at ``path``: none when there is no file yet, GrantsUnreadable when it cannot be read."""

    if path is None:
        return ()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ()
    except OSError as failure:
        raise GrantsUnreadable(f"skill-grants.json could not be read ({failure}).") from failure
    return parse_grants(text)


def lasting(grants: Iterable[Grant], skill: SkillRef) -> dict[str, Any]:
    """Everything active grants allow ``skill`` until they are withdrawn: every skill's, and its own.

    An item that allows anything counts only in a grant for this skill. The
    Hub never writes one into a grant for every skill; one that reaches the
    file another way allows nothing.
    """

    rows = []
    for grant in grants:
        if not grant.active or grant.once is not None or not grant.applies_to(skill):
            continue
        access, wide = grant.access, broad(grant.access) if grant.skill is None else {}
        rows.append({key: [item for item in value if item not in wide.get(key, ())] if isinstance(value, list) else value
                     for key, value in access.items()})
    return merged(rows)


def needed(requested: Mapping[str, Any] | None, grants: Iterable[Grant], skill: SkillRef, *,
           resolve: Resolve) -> dict[str, Any]:
    """What a run of ``skill`` declaring ``requested`` still needs a person to allow; ``{}`` when it may go ahead."""

    if not callable(resolve):
        raise TypeError("needed decides whether to ask, so it resolves folders as the runner does")
    return access_added(requested, lasting(grants, skill), resolve=resolve)


def once_grant(grants: Iterable[Grant], grant_id: str, skill: SkillRef, *, project_id: str, code_sha256: str,
               needs: Mapping[str, Any], now: datetime, used: Iterable[str], resolve: Resolve) -> Grant | None:
    """The one-run grant ``grant_id`` when it covers ``needs`` for exactly this project, skill and code, unused and unexpired."""

    taken = set(used)
    for grant in grants:
        if grant.grant_id != grant_id or not grant.active or grant.once is None or grant_id in taken:
            continue
        bound = grant.once
        if (bound.get("projectId"), bound.get("codeSha256")) != (project_id, code_sha256) or grant.skill != skill:
            return None
        expires = bound.get("expiresAt")
        if expires is None or datetime.fromisoformat(expires) <= now:
            return None
        return grant if access_within(needs, grant.access, resolve=resolve) else None
    return None


__all__ = [
    "ASKED", "GRANTS_ENV", "GRANTS_SCHEMA", "INTERPRETERS", "SOURCES", "Grant", "GrantsUnreadable", "SkillRef",
    "access_added", "access_within", "asked", "broad", "describe_added", "describe_added_zh", "empty", "lasting",
    "merged", "needed", "once_grant", "package_name", "parse_grants", "program_name", "read_grants",
]
