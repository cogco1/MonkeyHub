"""Skills: reusable procedures retained as immutable versions (#252).

A skill is what an architect or an agent wants done the same way again - how
to review a plan's hatching, how to set up a section sheet - written once as a
SKILL.md body with a name and a one-line description. The Hub hands the
current versions of the library project's skills to the agents through their
own native skill mechanism, which shows each name and description and reads a
body only when a skill is used. MonkeyHub builds no loader of its own.

Each version is retained in one fixed explicit run, ``studio-skills``, through
the same P036 ports every other Studio record uses. Versions are immutable: a
new one supersedes the old, and the old stays readable by its number. A skill
is only a procedure. It accepts no Stage, moves no HEAD and grants no
permission; the ``permissions`` its manifest lists are declared for a reader,
never enforced by anything here or in the agent that follows it.

A version may also carry script files, name one of them as its ``entry`` and
declare the ``access`` it needs (programs it calls, network, ssh hosts,
places it writes, Python packages). Which access a run may have is a person's
to allow, in the Hub and for every project (``skill_access``); the project
retains the exact version each run used (``skill_library.retain``), and
``codeSha256`` names body, entry, files and access together. A project's own
approvals of one version's code, from before S-1, stay retained with their
revocations: they allow no run, and the Hub reads those still in force once to
carry them over as grants for that skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PureWindowsPath, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SKILL, STUDIO_SKILL_APPROVAL, STUDIO_SKILL_REVOCATION
from archflow.project.repository import ProjectRepositoryError

from ..errors import StudioError
from ..authentication import ActorAttribution
from ..binding import ProjectBinding, retained_sources

SKILLS_RUN_ID = "studio-skills"
SKILL_SCHEMA = "StudioSkill@1"
SKILL_ID_PREFIX = "skill:"
# The agents' own limit on a skill name, and the only spelling both accept:
# lowercase words joined by single hyphens.
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
# A procedure, not a document store: a body over this is refused, not cut.
MAX_BODY_BYTES = 64 * 1024
MANIFEST_FIELDS = ("inputs", "outputs", "permissions")
APPROVAL_SCHEMA = "StudioSkillApproval@1"
# Script files a version may carry: small text, relative POSIX paths.
SKILL_FILE_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
MAX_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_FILES_BYTES = 2 * 1024 * 1024
APPROVAL_METHODS = frozenset({"hub-page", "hub-chat"})
APPROVAL_SCOPES = frozenset({"once", "version"})
REVOCATION_SCHEMA = "StudioSkillRevocation@1"
ACCESS_LISTS = ("programs", "ssh", "read", "write", "packages")
MAX_ACCESS_ITEMS = 64


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """One retained version: the P036 ref it is at, and what it says."""

    ref: str
    payload: Mapping[str, Any]

    @property
    def skill_id(self) -> str:
        return str(self.payload["skillId"])

    @property
    def version(self) -> int:
        return int(self.payload["version"])

    @property
    def entry(self) -> str | None:
        return self.payload.get("entry")

    @property
    def files(self) -> Mapping[str, str]:
        return self.payload.get("files") or {}

    @property
    def access(self) -> Mapping[str, Any] | None:
        return self.payload.get("access")

    @property
    def code_sha256(self) -> str:
        """Computed from what the version says, never read from a stored field."""

        return code_sha256(str(self.payload["body"]), self.entry, self.files, self.access)


def code_sha256(body: str, entry: str | None, files: Mapping[str, str],
                access: Mapping[str, Any] | None = None) -> str:
    """The identity a grant binds: body, entry, every file and the declared access, in one canonical form."""

    value: dict[str, Any] = {"body": body, "entry": entry, "files": dict(sorted(files.items()))}
    if access is not None:
        value["access"] = access
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _access(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What a version says it needs, in one checked form; nothing means it needs nothing."""

    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {*ACCESS_LISTS, "network"}:
        raise _invalid("access takes programs, network, ssh, read, write and packages.")
    access: dict[str, Any] = {"network": bool(value.get("network", False))}
    for key in ACCESS_LISTS:
        items = value.get(key) or []
        if not isinstance(items, list) or len(items) > MAX_ACCESS_ITEMS or any(
                not isinstance(item, str) or not item.strip() or len(item) > 300 for item in items):
            raise _invalid(f"access.{key} is a list of at most {MAX_ACCESS_ITEMS} short names.")
        items = [item.strip() for item in items]
        if key in ("read", "write"):
            for item in items:
                if item != "*" and not (PureWindowsPath(item).is_absolute() or PurePosixPath(item).is_absolute()):
                    raise _invalid(f"access.{key} names absolute folders or *, not {item!r}.")
        if key == "packages" and any(item.startswith("-") or any(c in item for c in "\r\n") for item in items):
            raise _invalid("access.packages names requirements such as numpy>=2, never pip options.")
        access[key] = items
    return access


def skill_id_for(name: str) -> str:
    return SKILL_ID_PREFIX + name


def _invalid(message: str) -> StudioError:
    return StudioError(422, "SKILL_INVALID", message)


def _not_found(skill_id: str) -> StudioError:
    return StudioError(404, "SKILL_NOT_FOUND", f"{skill_id} is not a skill this project retains.")


# ---- the fixed run ---------------------------------------------------------


def _run(binding: ProjectBinding, *, create: bool):
    """The skills run, asked for by name; created only by the first added skill.

    As with decisions, "not there" is one bounded question about this one run
    directory, so a damaged manifest refuses rather than reading as no skills.
    """

    if not binding.repository.layout.run(SKILLS_RUN_ID).root.is_dir():
        if not create:
            return None
        try:
            return binding.repository.create_run(SKILLS_RUN_ID)
        except (ProjectRepositoryError, OSError) as failure:
            raise StudioError(409, "SKILL_WRITE_FAILED",
                              "The skills run could not be created in this project.") from failure
    return binding.load_run(SKILLS_RUN_ID)


def _destination() -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=SKILLS_RUN_ID)


def _chains(binding: ProjectBinding) -> dict[str, tuple[SkillVersion, ...]]:
    """Each skill's versions, oldest first, or a refusal.

    A skill's versions are 1..n with no gap and no duplicate, and each one
    names the ref of the version it supersedes. Two writers that raced to the
    same number, or a missing version, refuse here rather than being resolved
    by picking a file.
    """

    run = _run(binding, create=False)
    if run is None:
        return {}
    grouped: dict[str, list[SkillVersion]] = {}
    for ref in binding.repository.list_json(run=run, destination=_destination(), record_kind=STUDIO_SKILL):
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != SKILL_SCHEMA or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "SKILL_BINDING_MISMATCH", "A retained skill belongs to another project.")
        row = SkillVersion(ref.uri, payload)
        grouped.setdefault(row.skill_id, []).append(row)
    chains: dict[str, tuple[SkillVersion, ...]] = {}
    for skill_id, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.version)
        if [row.version for row in ordered] != list(range(1, len(ordered) + 1)) or any(
            row.payload.get("previousVersionRef") != (None if index == 0 else ordered[index - 1].ref)
            for index, row in enumerate(ordered)
        ):
            raise StudioError(409, "SKILL_CONFLICT",
                              f"{skill_id} has competing or incomplete versions. Every version has been retained.")
        chains[skill_id] = tuple(ordered)
    return chains


# ---- validating one version ------------------------------------------------


def _single_line(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be non-empty text.")
    if len(value) > limit or re.search(r"[\x00-\x1f\x7f]", value):
        raise _invalid(f"{field} must be one line of at most {limit} characters.")
    return value.strip()


def _body(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("body must be the SKILL.md instructions.")
    if len(value.encode("utf-8")) > MAX_BODY_BYTES:
        raise StudioError(413, "SKILL_TOO_LARGE",
                          f"A skill body is at most {MAX_BODY_BYTES // 1024} KiB. Split the procedure or link its references.")
    if value.lstrip("﻿").startswith("---"):
        # The name and description are the frontmatter; a second one in the
        # body would say them twice and could say them differently.
        raise _invalid("body is the SKILL.md text after its frontmatter; name and description are their own fields.")
    return value


def _manifest(value: Mapping[str, Any] | None) -> dict[str, list[str]] | None:
    if value is None:
        return None
    return {field: [str(item) for item in value.get(field) or ()] for field in MANIFEST_FIELDS}


def _files(files: Mapping[str, Any] | None, entry: str | None) -> tuple[dict[str, str], str | None]:
    files = dict(files or {})
    if len(files) > MAX_FILES:
        raise _invalid(f"A skill carries at most {MAX_FILES} files.")
    total = 0
    for path, text in files.items():
        if not isinstance(path, str) or not SKILL_FILE_PATH.fullmatch(path) or ".." in path.split("/"):
            raise _invalid(f"{path!r} is not a relative file path such as scripts/run.py.")
        if not isinstance(text, str):
            raise _invalid(f"{path} must be text.")
        size = len(text.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise StudioError(413, "SKILL_TOO_LARGE", f"{path} is over {MAX_FILE_BYTES // 1024} KiB.")
        total += size
    if total > MAX_FILES_BYTES:
        raise StudioError(413, "SKILL_TOO_LARGE",
                          f"A skill's files are at most {MAX_FILES_BYTES // (1024 * 1024)} MiB together.")
    if entry is not None and (entry not in files or not entry.endswith(".py")):
        raise _invalid("entry must name one of the skill's .py files.")
    return files, entry


# ---- the public operations -------------------------------------------------


def list_skills(binding: ProjectBinding) -> tuple[SkillVersion, ...]:
    """Every skill's current version, by id."""

    return tuple(chain[-1] for _, chain in sorted(_chains(binding).items()))


def skill_versions(binding: ProjectBinding, skill_id: str) -> tuple[SkillVersion, ...]:
    """One skill's versions, oldest first."""

    chain = _chains(binding).get(skill_id)
    if chain is None:
        raise _not_found(skill_id)
    return chain


def read_skill(binding: ProjectBinding, skill_id: str, version: int | None = None) -> tuple[SkillVersion, int]:
    """One version (the latest when none is named), and the latest version number."""

    chain = skill_versions(binding, skill_id)
    if version is None:
        return chain[-1], chain[-1].version
    if not 1 <= version <= len(chain):
        raise StudioError(404, "SKILL_VERSION_NOT_FOUND", f"{skill_id} has no version {version}.")
    return chain[version - 1], chain[-1].version


def skill_content(*, name: str, description: str, body: str, manifest: Mapping[str, Sequence[str]] | None,
                  files: Mapping[str, str] | None, entry: str | None,
                  access: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """The skill id and the checked content a version of it retains, or the refusal that says what is wrong."""

    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or not SKILL_NAME.fullmatch(name):
        raise _invalid(f"name must be lowercase words joined by hyphens, at most {MAX_NAME_LENGTH} characters.")
    files, entry = _files(files, entry)
    content = {
        "name": name,
        "description": _single_line(description, "description", MAX_DESCRIPTION_LENGTH),
        "body": _body(body),
        "manifest": _manifest(manifest),
    }
    access = _access(access)
    if access is not None and entry is None:
        raise _invalid("access is declared by a runnable version: give it an entry.")
    if files or entry is not None:
        content |= {"entry": entry, "files": files}
        if access is not None:
            content["access"] = access
        content["codeSha256"] = code_sha256(content["body"], entry, files, access)
    return skill_id_for(name), content


def latest_version(binding: ProjectBinding, skill_id: str) -> SkillVersion | None:
    """The current version of a skill, or None when the project retains no skill of that id."""

    chain = _chains(binding).get(skill_id)
    return None if chain is None else chain[-1]


CONTENT_FIELDS = ("name", "description", "body", "manifest", "entry", "files", "access", "codeSha256")


def same_content(row: SkillVersion, content: Mapping[str, Any]) -> bool:
    """Whether a retained version says exactly what ``content`` says."""

    return all(row.payload.get(field) == content.get(field) for field in CONTENT_FIELDS)


@retained_sources
def add_skill(
    binding: ProjectBinding, *, name: str, description: str, body: str,
    manifest: Mapping[str, Sequence[str]] | None, supersedes_version: int | None,
    attribution: ActorAttribution, files: Mapping[str, str] | None = None, entry: str | None = None,
    access: Mapping[str, Any] | None = None,
) -> SkillVersion:
    """Retain one new version: a new skill, or the successor of the version the caller read.

    Adding a skill whose name is taken, or superseding a version that is no
    longer the latest, refuses: nobody's procedure is replaced by a writer who
    did not read it.
    """

    skill_id, content = skill_content(name=name, description=description, body=body, manifest=manifest,
                                      files=files, entry=entry, access=access)
    current = _chains(binding).get(skill_id)
    if current is None and supersedes_version is not None:
        raise _not_found(skill_id)
    if current is not None and supersedes_version is None:
        raise StudioError(409, "SKILL_EXISTS",
                          f"{skill_id} already exists at version {current[-1].version}. "
                          "Supersede that version to change it.")
    if current is not None and supersedes_version != current[-1].version:
        raise StudioError(409, "SKILL_STALE",
                          f"{skill_id} is at version {current[-1].version}. Read it before superseding it.")
    previous = None if current is None else current[-1]
    payload = {
        "schema": SKILL_SCHEMA,
        "projectId": binding.project_id,
        "skillId": skill_id,
        "version": 1 if previous is None else previous.version + 1,
        "previousVersionRef": None if previous is None else previous.ref,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "attribution": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                        "origin": attribution.origin},
        **content,
    }
    run = _run(binding, create=True)
    try:
        ref = binding.repository.put_json(run=run, destination=_destination(), record_kind=STUDIO_SKILL,
                                          payload=payload)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "SKILL_WRITE_FAILED", "The skill could not be retained in its project.") from exc
    return SkillVersion(ref.uri, payload)


# ---- approvals from before S-1: retained, read to carry them over ---------


def revocations(binding: ProjectBinding) -> dict[str, Mapping[str, Any]]:
    """Each withdrawn approval's revocation, by approval id."""

    run = _run(binding, create=False)
    if run is None:
        return {}
    rows = {}
    for ref in binding.repository.list_json(run=run, destination=_destination(), record_kind=STUDIO_SKILL_REVOCATION):
        payload = binding.repository.load_json(ref)
        if payload.get("schema") == REVOCATION_SCHEMA and payload.get("projectId") == binding.project_id:
            rows[str(payload["approvalId"])] = payload
    return rows


def approvals(binding: ProjectBinding, skill_id: str | None = None, *,
              include_revoked: bool = False) -> tuple[Mapping[str, Any], ...]:
    """Every retained approval in force (or every one, with ``include_revoked``), oldest first."""

    run = _run(binding, create=False)
    if run is None:
        return ()
    revoked = {} if include_revoked else revocations(binding)
    rows = []
    for ref in binding.repository.list_json(run=run, destination=_destination(), record_kind=STUDIO_SKILL_APPROVAL):
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != APPROVAL_SCHEMA or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "SKILL_BINDING_MISMATCH", "A retained skill approval belongs to another project.")
        if str(payload.get("approvalId")) in revoked:
            continue
        if skill_id is None or payload.get("skillId") == skill_id:
            rows.append(payload)
    return tuple(sorted(rows, key=lambda row: str(row.get("approvedAt"))))


def _scope(approval: Mapping[str, Any]) -> str:
    return str(approval.get("scope") or "version")


def grant_rows(binding: ProjectBinding, used_once: set[str]) -> list[dict[str, Any]]:
    """Every grant of the project with its status: active, used (a once grant a run took) or revoked."""

    withdrawn = revocations(binding)
    rows = []
    for item in approvals(binding, include_revoked=True):
        approval_id = str(item.get("approvalId"))
        row = {**item, "scope": _scope(item)}
        if approval_id in withdrawn:
            row |= {"status": "revoked", "revokedAt": withdrawn[approval_id]["revokedAt"]}
        elif row["scope"] == "once" and approval_id in used_once:
            row["status"] = "used"
        else:
            row["status"] = "active"
        rows.append(row)
    return rows
