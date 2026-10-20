"""Skill packages a project can take in: the ones shipped with MonkeyHub and the user's own.

A package is a folder holding ``skill.json`` (name, description, entry,
manifest, access), ``SKILL.md`` (the body; a frontmatter block is dropped) and
its other UTF-8 text files, which become the skill's files. Two places hold
packages: ``apps/monkeyhub/skills/``, shipped with each release, and
the user's ``%LOCALAPPDATA%\\MonkeyHub\\skill-packages`` (``MONKEYHUB_SKILL_PACKAGES``
names another folder). Neither is a project. Importing a package registers it
as a version of the bound project's skill of that name; a package that says
what the current version says adds nothing. Importing grants nothing: a person
still allows a version before it runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from ..authentication import ActorAttribution
from ..binding import ProjectBinding
from ..errors import StudioError
from . import skills

BUNDLED = Path(__file__).resolve().parents[5] / "apps" / "monkeyhub" / "skills"
SOURCES = ("bundled", "user")
FOLDER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def user_root() -> Path:
    """The user's own package folder."""

    override = os.environ.get("MONKEYHUB_SKILL_PACKAGES")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "MonkeyHub" / "skill-packages"


def roots() -> dict[str, Path]:
    return {"bundled": BUNDLED, "user": user_root()}


def _invalid(folder: Path, detail: str) -> StudioError:
    return StudioError(422, "SKILL_PACKAGE_INVALID", f"{folder.name}: {detail}")


def read_package(folder: Path) -> dict[str, Any]:
    """A package's skill as POST /api/skills takes it, or the refusal that says what is wrong with the folder."""

    try:
        spec = json.loads((folder / "skill.json").read_text(encoding="utf-8-sig"))
        body = (folder / "SKILL.md").read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise _invalid(folder, f"{Path(exc.filename).name} is missing.") from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _invalid(folder, f"skill.json or SKILL.md could not be read ({exc}).") from exc
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not isinstance(spec.get("description"), str):
        raise _invalid(folder, "skill.json gives name and description.")
    if body.startswith("---"):
        parts = body.split("---", 2)
        body = parts[2].lstrip("\r\n") if len(parts) == 3 else body
    files = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if (not path.is_file() or relative in ("skill.json", "SKILL.md")
                or any(part == "__pycache__" or part.startswith(".") for part in relative.split("/"))):
            continue
        try:
            files[relative] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise _invalid(folder, f"{relative} is not UTF-8 text; a package carries text files only.") from exc
    return {"name": spec["name"], "description": spec["description"], "body": body, "files": files,
            "entry": spec.get("entry"), "manifest": spec.get("manifest"), "access": spec.get("access")}


def _folder(source: str, name: str) -> Path:
    if source not in SOURCES:
        raise StudioError(422, "SKILL_PACKAGE_INVALID", f"source is one of {', '.join(SOURCES)}.")
    if not FOLDER.fullmatch(name):
        raise StudioError(422, "SKILL_PACKAGE_INVALID", "name is a package folder such as site-palette.")
    folder = roots()[source] / name
    if not (folder / "skill.json").is_file():
        raise StudioError(404, "SKILL_PACKAGE_NOT_FOUND", f"No {source} package is named {name} ({folder}).")
    return folder


def library(binding: ProjectBinding) -> dict[str, Any]:
    """Every package in both places, with what the bound project holds of each."""

    rows = []
    for source, root in roots().items():
        folders = sorted(path for path in root.iterdir() if (path / "skill.json").is_file()) if root.is_dir() else []
        for folder in folders:
            row: dict[str, Any] = {"source": source, "name": folder.name, "path": str(folder)}
            try:
                package = read_package(folder)
                skill_id, content = skills.skill_content(**package)
            except StudioError as error:
                rows.append({**row, "error": {"code": error.code, "detail": error.detail}})
                continue
            current = skills.latest_version(binding, skill_id)
            rows.append({**row, "skillId": skill_id, "skillName": content["name"],
                         "description": content["description"], "entry": content.get("entry"),
                         "access": content.get("access"), "codeSha256": content.get("codeSha256"),
                         "files": sorted(content.get("files") or ()),
                         "projectVersion": None if current is None else current.version,
                         "upToDate": current is not None and skills.same_content(current, content)})
    return {"projectId": binding.project_id, "sources": {key: str(path) for key, path in roots().items()},
            "packages": rows}


def import_package(binding: ProjectBinding, source: str, name: str, *,
                   attribution: ActorAttribution) -> tuple[skills.SkillVersion, bool]:
    """Register a package as the project's skill: a new version, or the current one when nothing changed."""

    package = read_package(_folder(source, name))
    skill_id, content = skills.skill_content(**package)
    current = skills.latest_version(binding, skill_id)
    if current is not None and skills.same_content(current, content):
        return current, False
    return skills.add_skill(binding, **package, supersedes_version=None if current is None else current.version,
                            attribution=attribution), True
