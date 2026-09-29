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
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Mapping
import uuid

from archflow.contracts.canonical import canonical_digest
from archflow_studio_api.settings import read_application_settings

from .models import HubFailure

PLUGIN_NAME = "monkeyhub-library"
# How long preparing the library's Runtime may take before the turn says so.
LIBRARY_BUDGET_S = 60.0


def plugin_root(runtime_root: Path) -> Path:
    return runtime_root / "cache" / "skill-plugins"


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


def library_plugin_dir(runtime_root: Path, hub_url: str) -> Path | None:
    """The plugin directory for the configured library, or None when no library is set.

    The library's Runtime is prepared and checked exactly as a chat's own
    project is, then its index is read; bodies are fetched only when this
    revision has not been built yet. Nothing is written into any project.
    """

    library = read_application_settings(runtime_root).library_dir
    if not library:
        return None
    # chat reaches this module from its CLI launch; the reverse import is late.
    from . import chat

    deadline = time.monotonic() + LIBRARY_BUDGET_S
    try:
        project_id, project_dir = chat._project(library)
        chat._prepare_studio(hub_url, {"projectId": project_id, "projectDir": project_dir}, LIBRARY_BUDGET_S)
        base, _ = chat._bound_studio(hub_url, None, project_id=project_id, project_dir=project_dir,
                                     deadline=deadline)
        index = chat._request_json(base, "/api/skills", timeout=max(1.0, deadline - time.monotonic()))
        if index.get("projectId") != project_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The library's Runtime answered for another project.")
        return materialize(
            plugin_root(runtime_root), index,
            lambda skill_id, version: chat._request_json(
                base, f"/api/skills/{skill_id}?version={version}", timeout=max(1.0, deadline - time.monotonic())),
        )
    except HubFailure as failure:
        raise HubFailure(failure.status, "CHAT_SKILL_LIBRARY_UNAVAILABLE",
                         f"The skill library could not be read: {failure.error.detail} "
                         "Open the library project, or clear it in Settings.") from failure
    except OSError as exc:
        raise HubFailure(503, "CHAT_SKILL_LIBRARY_UNAVAILABLE",
                         "The skill library could not be read or its skills could not be prepared. "
                         "Open the library project, or clear it in Settings.") from exc
