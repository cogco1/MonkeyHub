"""The library project's skills, handed to Claude as a plugin of its own (#252).

A library is an ordinary complete project named in the application settings.
Its skills are retained there by ``studio.skills``; the Hub reads them through
the library's own Project Runtime, the way it reads any project, and never
opens the project's files itself. What it writes is a plugin directory in its
own non-canonical cache::

    <runtime root>/cache/skill-plugins/<index digest>/
        .claude-plugin/plugin.json
        skills/<name>/SKILL.md

``--plugin-dir`` loads it for one Claude session. Claude then shows each
skill's name and description and reads a body only when the skill is used: the
agent's own mechanism, with no MonkeyHub loader. The directory is named by the
digest of the index (ids, versions, names and descriptions), so it is built
once per library revision and a chat on an unchanged library fetches no body.
A skill is only a procedure: it grants no permission, and the permissions its
manifest declares are not enforced by anything here.

A project's ``recipe`` memory names one skill as ``skill:<name>@<version>``.
The Hub resolves that name against the same current index when a chat saves
one (``pin``), and says of every recipe a turn carries whether its version is
still the library's current one (``pinned_status``): nothing is guessed or
swapped, the agent tells the user.

A chat sees the library's skills and nothing else (#463). With no library,
``chat`` starts Claude with ``--disable-slash-commands``, which removes the
Skill tool. With one, it adds ``--setting-sources project,local`` (no user
settings: no personal skills or installed plugins) and ``--settings`` from
``claude_settings``: ``disableBundledSkills`` plus a ``skillOverrides`` entry
``"off"`` for every skill still left over. The CLI ignores the whole
``--settings`` value when an override is anything but the string ``"off"``.
The leftovers are learned, not guessed: a turn's init event lists the skills
that session can load, and any that is not the library's is recorded in
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
import time
from typing import Any, Callable, Mapping
import uuid

from archflow.contracts.canonical import canonical_digest
from archflow_studio_api.settings import read_application_settings

from .models import HubFailure

PLUGIN_NAME = "monkeyhub-library"
# Skills --setting-sources and disableBundledSkills leave in place, on Claude
# Code 2.1.283. Later versions' leftovers are learned from their init events.
LEFTOVER_SEED = ("design", "doctor")
# The user settings a library chat still carries: its sign-in and network.
CARRIED_SETTINGS = ("env", "apiKeyHelper")
_log = logging.getLogger(__name__)
_leftover_lock = threading.Lock()
# How long preparing the library's Runtime may take before the turn says so.
LIBRARY_BUDGET_S = 60.0
# The spelling studio.skills accepts when a skill is saved. A name read back is
# a folder name here, so one that is not this spelling (a hand-edited record)
# is refused rather than written as a path.
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# How a chat may name a skill for a recipe: as the agent sees it in the plugin,
# as a library ref, or bare; the version is optional and the Hub fills it.
_NAMED_SKILL = re.compile(rf"(?:{PLUGIN_NAME}:|skill:)?(?P<name>[^@:]+)(?:@(?P<version>[1-9][0-9]{{0,8}}))?")
_PINNED_SKILL = re.compile(r"skill:(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)@(?P<version>[1-9][0-9]{0,8})")


@dataclass(frozen=True, slots=True)
class Library:
    """The configured library's current index, read through its Runtime, and how to read one version."""

    index: Mapping[str, Any]
    fetch: Callable[[str, int], Mapping[str, Any]]

    def current(self, name: str) -> Mapping[str, Any] | None:
        """The index row of the skill with this name, at its current version."""

        return next((row for row in self.index.get("skills", ()) if row.get("name") == name), None)


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


def claude_settings(runtime_root: Path, user_settings: Mapping[str, Any]) -> str:
    """The ``--settings`` JSON a library chat starts with.

    ``user_settings`` is the user's own Claude ``settings.json``; only its
    ``env`` and ``apiKeyHelper`` are carried, since ``--setting-sources
    project,local`` drops the rest along with the personal skills and plugins.
    """

    settings: dict[str, Any] = {key: user_settings[key] for key in CARRIED_SETTINGS if key in user_settings}
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


def index_digest(index: Mapping[str, Any]) -> str:
    """What names one library revision: its project and every current version's index row."""

    rows = sorted(
        [str(row["id"]), int(row["version"]), str(row["name"]), str(row["description"])]
        for row in index.get("skills", ())
    )
    return canonical_digest({"projectId": str(index["projectId"]), "skills": rows})


def skill_markdown(skill: Mapping[str, Any]) -> str:
    """One SKILL.md: the frontmatter an agent lists, then the body it reads on use."""

    # A JSON string is a YAML flow scalar, so any description stays one value.
    return (f"---\nname: {skill['name']}\ndescription: {json.dumps(skill['description'], ensure_ascii=False)}\n"
            f"---\n\n{skill['body'].rstrip()}\n")


def materialize(cache_root: Path, index: Mapping[str, Any],
                fetch: Callable[[str, int], Mapping[str, Any]]) -> Path | None:
    """The plugin directory for this index, built only when no complete one exists yet.

    ``fetch(skill_id, version)`` returns one exact version with its body. A
    library with no skills has nothing to load and gives no directory. The
    directory is written beside its final name and renamed into place, so a
    reader never sees half a plugin.
    """

    rows = list(index.get("skills", ()))
    if not rows:
        return None
    digest = index_digest(index)
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
            "description": f"Skills from the MonkeyHub library project {index['projectId']}.",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for row in rows:
            skill = fetch(str(row["id"]), int(row["version"]))
            if (skill.get("id"), skill.get("version"), skill.get("name")) != (row["id"], row["version"], row["name"]):
                raise HubFailure(409, "CHAT_SKILL_LIBRARY_CHANGED",
                                 "The skill library changed while its skills were being read. Retry the message.")
            if not isinstance(skill["name"], str) or len(skill["name"]) > 64 or not SKILL_NAME.fullmatch(skill["name"]):
                raise HubFailure(409, "CHAT_SKILL_LIBRARY_INVALID",
                                 f"The library holds a skill named {str(skill['name'])[:80]!r}, which is not a skill "
                                 "name (lowercase words joined by hyphens).")
            folder = staging / "skills" / str(skill["name"])
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(skill_markdown(skill), encoding="utf-8")
        try:
            staging.rename(target)
        except OSError:
            # Another chat built the same revision first; its copy is the same.
            if not manifest.is_file():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _unavailable(failure: HubFailure | OSError) -> HubFailure:
    if isinstance(failure, HubFailure):
        return HubFailure(failure.status, "CHAT_SKILL_LIBRARY_UNAVAILABLE",
                          f"The skill library could not be read: {failure.error.detail} "
                          "Open the library project, or clear it in Settings.")
    return HubFailure(503, "CHAT_SKILL_LIBRARY_UNAVAILABLE",
                      "The skill library could not be read or its skills could not be prepared. "
                      "Open the library project, or clear it in Settings.")


def read_library(library_dir: str | None, hub_url: str) -> Library | None:
    """The current index of the library project at ``library_dir``, or None when no library is set.

    The library's Runtime is prepared and checked exactly as a chat's own
    project is, then its index is read. Nothing is written into any project.
    """

    if not library_dir:
        return None
    # chat reaches this module from its CLI launch; the reverse import is late.
    from . import chat

    deadline = time.monotonic() + LIBRARY_BUDGET_S
    try:
        project_id, project_dir = chat._project(library_dir)
        chat._prepare_studio(hub_url, {"projectId": project_id, "projectDir": project_dir}, LIBRARY_BUDGET_S)
        base, _ = chat._bound_studio(hub_url, None, project_id=project_id, project_dir=project_dir,
                                     deadline=deadline)
        index = chat._request_json(base, "/api/skills", timeout=max(1.0, deadline - time.monotonic()))
        if index.get("projectId") != project_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The library's Runtime answered for another project.")
    except (HubFailure, OSError) as failure:
        raise _unavailable(failure) from failure
    return Library(index, lambda skill_id, version: chat._request_json(
        base, f"/api/skills/{skill_id}?version={version}", timeout=max(1.0, deadline - time.monotonic())))


def configured_library(runtime_root: Path, hub_url: str) -> Library | None:
    """The library the application settings name, read now; None when none is set."""

    return read_library(read_application_settings(runtime_root).library_dir, hub_url)


def plugin_dir(runtime_root: Path, library: Library | None) -> Path | None:
    """The plugin directory for this library's index, or None when there is none to load.

    Bodies are fetched only when this revision has not been built yet.
    """

    if library is None:
        return None
    try:
        return materialize(plugin_root(runtime_root), library.index, library.fetch)
    except (HubFailure, OSError) as failure:
        raise _unavailable(failure) from failure


def library_plugin_dir(runtime_root: Path, hub_url: str) -> Path | None:
    """The plugin directory for the configured library, or None when no library is set."""

    return plugin_dir(runtime_root, configured_library(runtime_root, hub_url))


def pin(library: Library | None, named: Any) -> str:
    """The exact ``skill:<name>@<version>`` a recipe saves for a skill the agent named.

    The agent names it as it sees it (``monkeyhub-library:hatch-review``), as a
    ref, or bare, with or without a version; the version is the library's
    current one. Refused with its reason when no library is set, the library
    has no such skill, or a named version is not the current one.
    """

    found = _NAMED_SKILL.fullmatch(named.strip()) if isinstance(named, str) else None
    if found is None or not SKILL_NAME.fullmatch(found["name"]) or len(found["name"]) > 64:
        raise HubFailure(422, "CHAT_RECIPE_SKILL_INVALID",
                         f"A recipe names one library skill as {PLUGIN_NAME}:<name> (the name the skill list shows), "
                         "optionally with @<version>.")
    if library is None:
        raise HubFailure(409, "CHAT_RECIPE_NO_LIBRARY",
                         "No skill library is set, so a recipe has no skill to name. Tell the user: the library "
                         "project is chosen in Settings; nothing was saved.")
    row = library.current(found["name"])
    if row is None:
        names = ", ".join(sorted(str(row["name"]) for row in library.index.get("skills", ()))) or "none"
        raise HubFailure(404, "CHAT_RECIPE_SKILL_UNKNOWN",
                         f"The skill library has no skill named {found['name']!r} (its skills: {names}). "
                         "Nothing was saved; tell the user rather than naming another.")
    version = int(row["version"])
    if found["version"] is not None and int(found["version"]) != version:
        raise HubFailure(409, "CHAT_RECIPE_SKILL_VERSION",
                         f"The library's current {found['name']} is version {version}, not {found['version']}. "
                         "A recipe pins the current version: omit the version.")
    return f"skill:{found['name']}@{version}"


def pinned_status(library: Library | None, pinned: str, *, loadable: bool = True) -> dict[str, Any]:
    """What a turn is told about one recipe's skill: the name to load and whether its version is current.

    ``loadable`` is false for a provider the library plugin is not handed to.
    """

    found = _PINNED_SKILL.fullmatch(pinned)
    name, version = (found["name"], int(found["version"])) if found else (pinned, None)
    status = {"load": f"{PLUGIN_NAME}:{name}" if loadable else None, "pinned": pinned}
    row = None if library is None or found is None else library.current(name)
    if row is None:
        why = "no skill library is set" if library is None else "the library has no skill by that name"
        return {**status, "libraryVersion": None,
                "note": f"not in the library ({why}): tell the user; do not follow another skill in its place."}
    current = int(row["version"])
    if current != version:
        return {**status, "libraryVersion": current,
                "note": f"pinned {version}, library now {current}: tell the user which one to follow; the skill "
                        "you load is the library's current version."}
    return {**status, "libraryVersion": current,
            "note": f"pinned {version} is the library's current version" + (
                "." if loadable else ", but this chat's provider loads no library skills: tell the user.")}
