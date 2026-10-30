"""The skills installed on this machine, handed to Claude as a plugin of its own (S-1, #252).

Every Hub chat is given the same skills: the ones shipped with MonkeyHub and
the person's own, read from the skill folders
(``project_runtime.application.skill_library.installed``, which reads no
project, so no Project Runtime has to answer first and none can be the wrong
one). What the Hub writes is a plugin directory in its own non-canonical cache::

    <runtime root>/cache/skill-plugins/<index digest>/
        .claude-plugin/plugin.json
        skills/<name>/SKILL.md

``--plugin-dir`` loads it for one Claude session. Claude then shows each
skill's name and description and reads a body only when the skill is used: the
agent's own mechanism, with no MonkeyHub loader. The directory is named by the
digest of the index (names, descriptions and code), so it is built once per
change of the folders. A skill is only a procedure: it grants no permission;
what a run may do is a person's to allow (``settings.skill_grants``).

A project's ``recipe`` memory names one skill as ``skill:<name>@<code>``, the
first 12 hex digits of its codeSha256 (``pin``), and the Hub says of every
recipe a turn carries whether that skill still has that code
(``pinned_status``): nothing is guessed or swapped, the agent tells the user.
A recipe from before S-1 names a library version (``skill:<name>@<n>``); it is
said to come from before, and the installed skill of that name is the one loaded.

A chat sees these skills and nothing else (#463). Either way ``chat``
starts Claude with ``--setting-sources project,local``: no user settings, so no
personal skills, installed plugins or their hooks. With no skill installed it adds
``--disable-slash-commands``, which removes the Skill tool. With some it adds
``--settings`` from ``claude_settings``: ``disableBundledSkills`` plus a ``skillOverrides`` entry
``"off"`` for every skill still left over. The CLI ignores the whole
``--settings`` value when an override is anything but the string ``"off"``.
The leftovers are learned, not guessed: a turn's init event lists the skills
that session can load, and any that is not the plugin's is recorded in
``leftover-skills.json`` beside the plugins (non-canonical cache, keyed by the
CLI's version) and turned off from the next turn on.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Mapping
import uuid

from archflow.contracts.canonical import canonical_digest
from project_runtime.application.skill_library import installed

from ..models import HubFailure

PLUGIN_NAME = "monkeyhub-library"
# Skills --setting-sources and disableBundledSkills leave in place, on Claude
# Code 2.1.283. Later versions' leftovers are learned from their init events.
LEFTOVER_SEED = ("design", "doctor")
# The user settings a library chat still carries: its sign-in and network.
CARRIED_SETTINGS = ("env", "apiKeyHelper")
_log = logging.getLogger(__name__)
_leftover_lock = threading.Lock()
# The spelling project_runtime.skills accepts when a skill is saved. A name read back is
# a folder name here, so one that is not this spelling (a hand-edited record)
# is refused rather than written as a path.
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# How a chat may name a skill for a recipe: as the agent sees it in the plugin,
# as a library ref, or bare; the version is optional and the Hub fills it.
_NAMED_SKILL = re.compile(rf"(?:{PLUGIN_NAME}:|skill:)?(?P<name>[^@:]+)(?:@(?P<pinned>[0-9a-f]{{12}}|[1-9][0-9]{{0,8}}))?")
_PINNED_SKILL = re.compile(r"skill:(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)@(?:(?P<code>[0-9a-f]{12})|(?P<version>[1-9][0-9]{0,8}))")
# How a recipe names a skill's code: the first 12 hex digits of its codeSha256.
CODE_DIGITS = 12


@dataclass(frozen=True, slots=True)
class Library:
    """The installed skills a chat is given: one row per name (id, name, description, code, source, body)."""

    skills: tuple[Mapping[str, Any], ...]

    def current(self, name: str) -> Mapping[str, Any] | None:
        """The row of the skill with this name, as the folders hold it now."""

        return next((row for row in self.skills if row.get("name") == name), None)


def installed_library() -> Library | None:
    """The skills installed on this machine, read now; None when no folder holds a readable one.

    A package that cannot be read is left out here: it is listed with why in
    the developer Skills list, and runs nothing.
    """

    rows = []
    for row in installed().current():
        if row.content is None:
            continue
        rows.append({"id": row.skill_id, "name": row.name, "description": row.content["description"],
                     "code": str(row.code_sha256)[:CODE_DIGITS], "source": row.source, "body": row.content["body"]})
    return Library(tuple(rows)) if rows else None


def plugin_root(runtime_root: Path) -> Path:
    return runtime_root / "cache" / "skill-plugins"


def leftovers_path(runtime_root: Path) -> Path:
    return plugin_root(runtime_root) / "leftover-skills.json"


def _recorded(runtime_root: Path) -> dict[str, list[str]]:
    try:
        saved = json.loads(leftovers_path(runtime_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {str(version): [name for name in names if isinstance(name, str)]
            for version, names in saved.items() if isinstance(names, list)}


def leftovers(runtime_root: Path) -> list[str]:
    """Every skill a library chat turns off: the seed and each one learned, under any CLI version.

    A name the running CLI does not have is tolerated in ``skillOverrides``.
    """

    names = set(LEFTOVER_SEED)
    for recorded in _recorded(runtime_root).values():
        names.update(recorded)
    return sorted(names)


def carried_settings(user_settings: Mapping[str, Any]) -> dict[str, Any]:
    """What of the user's own Claude ``settings.json`` a Hub chat keeps: sign-in and network only."""

    return {key: user_settings[key] for key in CARRIED_SETTINGS if key in user_settings}


def claude_settings(runtime_root: Path, user_settings: Mapping[str, Any]) -> str:
    """The ``--settings`` JSON a library chat starts with.

    ``user_settings`` is the user's own Claude ``settings.json``; only its
    ``env`` and ``apiKeyHelper`` are carried, since ``--setting-sources
    project,local`` drops the rest along with the personal skills and plugins.
    """

    settings = carried_settings(user_settings)
    settings["disableBundledSkills"] = True
    settings["skillOverrides"] = {name: "off" for name in leftovers(runtime_root)}
    return json.dumps(settings, ensure_ascii=False)


def learn(runtime_root: Path, event: Mapping[str, Any]) -> list[str]:
    """Record the skills a library chat's init event lists besides the library's; return the new ones.

    Each new one is logged once, and ``claude_settings`` turns it off from the
    next turn on.
    """

    listed = event.get("skills")
    if not isinstance(listed, list):
        return []
    version = str(event.get("claude_code_version") or "unknown")
    with _leftover_lock:
        known = set(leftovers(runtime_root))
        found = sorted({name for name in listed if isinstance(name, str) and name not in known
                        and not name.startswith(f"{PLUGIN_NAME}:")})
        if not found:
            return []
        recorded = _recorded(runtime_root)
        recorded[version] = sorted({*recorded.get(version, ()), *found})
        target = leftovers_path(runtime_root)
        staging = target.with_name(f".{target.name}-{uuid.uuid4().hex}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.write_text(json.dumps(recorded, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
            os.replace(staging, target)
        except OSError as failure:
            # The turn goes on; the skill stays listed until a record succeeds.
            _log.warning("Could not record leftover skills %s: %s", found, failure)
            return []
        finally:
            staging.unlink(missing_ok=True)
    for name in found:
        _log.warning("Claude Code %s lists the skill %r in a library chat; it is turned off from the next turn.",
                     version, name)
    return found


def index_digest(library: Library) -> str:
    """What names one state of the skill folders, as a chat is given them."""

    rows = sorted([str(row["id"]), str(row["name"]), str(row["description"]), str(row["code"]), str(row["source"])]
                  for row in library.skills)
    return canonical_digest({"skills": rows})


def skill_markdown(skill: Mapping[str, Any]) -> str:
    """One SKILL.md: the frontmatter an agent lists, then the body it reads on use."""

    # A JSON string is a YAML flow scalar, so any description stays one value.
    return (f"---\nname: {skill['name']}\ndescription: {json.dumps(skill['description'], ensure_ascii=False)}\n"
            f"---\n\n{skill['body'].rstrip()}\n")


def materialize(cache_root: Path, library: Library) -> Path | None:
    """The plugin directory for these skills, built only when no complete one exists yet.

    No skill installed gives no directory. The directory is written beside its
    final name and renamed into place, so a reader never sees half a plugin.
    """

    if not library.skills:
        return None
    digest = index_digest(library)
    target = cache_root / digest[:32]
    manifest = target / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        return target
    staging = cache_root / f".{digest[:32]}-{uuid.uuid4().hex}"
    try:
        (staging / ".claude-plugin").mkdir(parents=True)
        (staging / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": PLUGIN_NAME,
            "author": {"name": "MonkeyHub"},
            "version": f"0.0.0-{digest[:12]}",
            "description": "The skills installed in MonkeyHub on this machine.",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for skill in library.skills:
            if not isinstance(skill["name"], str) or len(skill["name"]) > 64 or not SKILL_NAME.fullmatch(skill["name"]):
                raise HubFailure(409, "CHAT_SKILL_LIBRARY_INVALID",
                                 f"A skill folder holds a skill named {str(skill['name'])[:80]!r}, which is not a skill "
                                 "name (lowercase words joined by hyphens).")
            folder = staging / "skills" / str(skill["name"])
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(skill_markdown(skill), encoding="utf-8")
        try:
            staging.rename(target)
        except OSError:
            # Another chat built the same skills first; its copy is the same.
            if not manifest.is_file():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def plugin_dir(runtime_root: Path, library: Library | None) -> Path | None:
    """The plugin directory for these skills, or None when there is none to load."""

    if library is None:
        return None
    try:
        return materialize(plugin_root(runtime_root), library)
    except OSError as failure:
        raise HubFailure(503, "CHAT_SKILL_LIBRARY_UNAVAILABLE",
                         "The installed skills could not be prepared for this chat.") from failure


def pin(library: Library | None, named: Any) -> str:
    """The exact ``skill:<name>@<code>`` a recipe saves for a skill the agent named.

    The agent names it as it sees it (``monkeyhub-library:hatch-review``), as a
    ref, or bare, with or without a code; the code is the installed skill's
    current one. Refused with its reason when no skill is installed, none has
    that name, or a named code or version is not the current code.
    """

    found = _NAMED_SKILL.fullmatch(named.strip()) if isinstance(named, str) else None
    if found is None or not SKILL_NAME.fullmatch(found["name"]) or len(found["name"]) > 64:
        raise HubFailure(422, "CHAT_RECIPE_SKILL_INVALID",
                         f"A recipe names one installed skill as {PLUGIN_NAME}:<name> (the name the skill list shows).")
    if library is None:
        raise HubFailure(409, "CHAT_RECIPE_NO_LIBRARY",
                         "No skill is installed, so a recipe has no skill to name. Tell the user: a skill is installed "
                         "by putting its folder in the skill folder; nothing was saved.")
    row = library.current(found["name"])
    if row is None:
        names = ", ".join(sorted(str(row["name"]) for row in library.skills)) or "none"
        raise HubFailure(404, "CHAT_RECIPE_SKILL_UNKNOWN",
                         f"No installed skill is named {found['name']!r} (installed: {names}). "
                         "Nothing was saved; tell the user rather than naming another.")
    code = str(row["code"])
    if found["pinned"] is not None and found["pinned"] != code:
        raise HubFailure(409, "CHAT_RECIPE_SKILL_VERSION",
                         f"The installed {found['name']} has code {code}, not {found['pinned']}. "
                         "A recipe pins the current code: omit it.")
    return f"skill:{found['name']}@{code}"


def pinned_status(library: Library | None, pinned: str, *, loadable: bool = True) -> dict[str, Any]:
    """What a turn is told about one recipe's skill: the name to load and whether its code is the one pinned.

    ``loadable`` is false for a provider the skills plugin is not handed to.
    """

    found = _PINNED_SKILL.fullmatch(pinned)
    name = found["name"] if found else pinned
    status = {"load": f"{PLUGIN_NAME}:{name}" if loadable else None, "pinned": pinned}
    row = None if library is None or found is None else library.current(name)
    if row is None:
        why = "no skill is installed" if library is None else "no installed skill has that name"
        return {**status, "current": None,
                "note": f"not installed ({why}): tell the user; do not follow another skill in its place."}
    current = str(row["code"])
    if found["version"] is not None:
        return {**status, "current": current,
                "note": f"saved before skills came from the skill folder (library version {found['version']}); the "
                        "installed skill of that name is the one you load: tell the user."}
    if found["code"] != current:
        return {**status, "current": current,
                "note": f"pinned code {found['code']}, installed now {current}: the skill changed since the recipe "
                        "was saved; tell the user which one they want."}
    return {**status, "current": current,
            "note": "the installed skill has the pinned code" + (
                "." if loadable else ", but this chat's provider loads no installed skills: tell the user.")}
