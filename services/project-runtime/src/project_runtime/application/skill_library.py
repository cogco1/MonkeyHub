"""The skills installed on this machine: the ones shipped with MonkeyHub and the user's own (S-1).

A package is a folder holding ``skill.json`` (name, description, entry,
manifest, access), ``SKILL.md`` (the body; a frontmatter block is dropped) and
its other UTF-8 text files, which become the skill's files. Two places hold
packages: ``apps/monkeyhub/skills/``, shipped with each release, and the
user's ``%LOCALAPPDATA%\\MonkeyHub\\skill-packages`` (``MONKEYHUB_SKILL_PACKAGES``
names another folder). Neither is a project, and every project can use what
they hold: nothing is imported first. A name found in both places is the
user's, as a person's own skill is in Claude. A package that cannot be read is
listed with why, and runs nothing.

Reading them needs no project (``installed``): the Hub lists them for its
chats without starting a Project Runtime, which is why the Hub may read this
module (architecture_policy, the Runtime's declared read API). A Runtime finds
the one a run names, and the project retains that version's exact content the
first time it runs there (``retain``), so the run record names code the
project still holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
# The order a name is looked up in: a person's own skill comes before the shipped one of that name.
SOURCES = ("user", "bundled")
FOLDER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def user_root() -> Path:
    """The user's own package folder."""

    override = os.environ.get("MONKEYHUB_SKILL_PACKAGES")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "MonkeyHub" / "skill-packages"


def roots() -> dict[str, Path]:
    return {"user": user_root(), "bundled": BUNDLED}


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    """One package as found: where, and its checked content or why it has none."""

    name: str
    source: str
    folder: Path
    package: Mapping[str, Any] | None
    content: Mapping[str, Any] | None
    error: Mapping[str, str] | None

    @property
    def skill_id(self) -> str:
        return skills.skill_id_for(self.name)

    @property
    def code_sha256(self) -> str | None:
        """What the package says, as one hash; a package of instructions only is hashed as its body."""

        if self.content is None:
            return None
        return self.content.get("codeSha256") or skills.code_sha256(str(self.content["body"]), None, {})

    @property
    def access(self) -> Mapping[str, Any] | None:
        return None if self.content is None else self.content.get("access")


@dataclass(frozen=True, slots=True)
class Installed:
    """Every package in the skill folders, in lookup order."""

    roots: Mapping[str, Path]
    packages: tuple[InstalledSkill, ...]

    def find(self, name: str) -> InstalledSkill | None:
        """The package a name means: the user's when both places hold one, even when the user's cannot be read."""

        return next((row for row in self.packages if row.name == name), None)

    def current(self) -> tuple[InstalledSkill, ...]:
        """One package per name, the one ``find`` answers."""

        seen: set[str] = set()
        rows = []
        for row in self.packages:
            if row.name not in seen:
                seen.add(row.name)
                rows.append(row)
        return tuple(rows)


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


# A path in SKILL.md that names a file the package should carry: under one of the folders a skill keeps
# its helpers in, written out in full. A path with a placeholder (params/<style>.json) names no one file.
_MENTIONED_FILE = re.compile(
    r"(?<![\w/.<{-])((?:scripts|params|assets|references|templates|data)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
    r"\.[A-Za-z0-9]+)(?![\w<{*])")


def package_problem(package: dict[str, Any]) -> dict[str, Any] | None:
    """What is wrong with a readable package that would only show when a run starts, or None.

    The entry script must compile, and every file SKILL.md names by its full path under scripts/, params/,
    assets/, references/, templates/ or data/ must be in the package. ``kind`` is one of ``entry_syntax``
    (``file`` and ``line``) or ``missing_file`` (``file``); ``detail`` says it in one line.
    """

    entry, files = package.get("entry"), package.get("files") or {}
    if entry is not None and entry in files:
        try:
            # A script saved by a Windows editor starts with a byte order mark: Python runs the file, so it is no
            # reason to refuse the package. Only the check drops it; the file and its digest stay as they are.
            compile(files[entry].removeprefix("\ufeff"), entry, "exec", dont_inherit=True)
        except SyntaxError as failure:
            return {"kind": "entry_syntax", "file": entry, "line": failure.lineno,
                    "detail": f"{entry} does not compile: line {failure.lineno}, {failure.msg}."}
    for mentioned in dict.fromkeys(match.group(1) for match in _MENTIONED_FILE.finditer(str(package["body"]))):
        if mentioned not in files:
            return {"kind": "missing_file", "file": mentioned,
                    "detail": f"SKILL.md names {mentioned}, which the package does not carry."}
    return None


def _read(source: str, folder: Path) -> InstalledSkill:
    try:
        package = read_package(folder)
        skill_id, content = skills.skill_content(**package)
    except StudioError as error:
        return InstalledSkill(folder.name, source, folder, None, None,
                              {"code": error.code, "detail": error.detail, "kind": "unreadable"})
    name = skill_id.removeprefix(skills.SKILL_ID_PREFIX)
    if name != folder.name:
        return InstalledSkill(folder.name, source, folder, None, None, {
            "code": "SKILL_PACKAGE_INVALID", "kind": "name_differs", "name": name,
            "detail": f"{folder.name}: skill.json names it {name}; the folder is its name."})
    problem = package_problem(package)
    if problem is not None:
        return InstalledSkill(name, source, folder, None, None, {
            "code": "SKILL_PACKAGE_INVALID", **problem, "detail": f"{name}: {problem['detail']}"})
    return InstalledSkill(name, source, folder, package, content, None)


def installed() -> Installed:
    """Every package in the skill folders, read now; no project is involved."""

    found = roots()
    rows = []
    for source in SOURCES:
        root = found[source]
        folders = sorted(path for path in root.iterdir() if FOLDER.fullmatch(path.name)
                         and (path / "skill.json").is_file()) if root.is_dir() else []
        rows += [_read(source, folder) for folder in folders]
    return Installed(found, tuple(rows))


def library(binding: ProjectBinding) -> dict[str, Any]:
    """Every package in both places, with what the bound project holds of each."""

    rows = []
    shadowed = set()
    for row in installed().packages:
        line: dict[str, Any] = {"source": row.source, "name": row.name, "path": str(row.folder),
                                "shadowed": row.name in shadowed}
        shadowed.add(row.name)
        if row.content is None:
            rows.append({**line, "error": dict(row.error or {})})
            continue
        current = skills.latest_version(binding, row.skill_id)
        rows.append({**line, "skillId": row.skill_id, "skillName": row.name,
                     "description": row.content["description"], "entry": row.content.get("entry"),
                     "access": row.access, "codeSha256": row.code_sha256,
                     "files": sorted(row.content.get("files") or ()),
                     "projectVersion": None if current is None else current.version,
                     "upToDate": current is not None and skills.same_content(current, row.content)})
    return {"projectId": binding.project_id, "sources": {key: str(path) for key, path in roots().items()},
            "packages": rows}


def retain(binding: ProjectBinding, skill: InstalledSkill, *, attribution: ActorAttribution) -> skills.SkillVersion:
    """The project's version of exactly this package: the current one when it says the same, else a new one."""

    if skill.package is None or skill.content is None:
        error = skill.error or {}
        raise StudioError(422, error.get("code", "SKILL_PACKAGE_INVALID"), error.get("detail", f"{skill.name} cannot be read."))
    current = skills.latest_version(binding, skill.skill_id)
    if current is not None and skills.same_content(current, skill.content):
        return current
    return skills.add_skill(binding, **skill.package, supersedes_version=None if current is None else current.version,
                            attribution=attribution)
