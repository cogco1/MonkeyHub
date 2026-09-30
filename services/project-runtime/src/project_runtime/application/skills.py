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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SKILL
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


@retained_sources
def add_skill(
    binding: ProjectBinding, *, name: str, description: str, body: str,
    manifest: Mapping[str, Sequence[str]] | None, supersedes_version: int | None,
    attribution: ActorAttribution,
) -> SkillVersion:
    """Retain one new version: a new skill, or the successor of the version the caller read.

    Adding a skill whose name is taken, or superseding a version that is no
    longer the latest, refuses: nobody's procedure is replaced by a writer who
    did not read it.
    """

    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or not SKILL_NAME.fullmatch(name):
        raise _invalid(f"name must be lowercase words joined by hyphens, at most {MAX_NAME_LENGTH} characters.")
    skill_id = skill_id_for(name)
    content = {
        "name": name,
        "description": _single_line(description, "description", MAX_DESCRIPTION_LENGTH),
        "body": _body(body),
        "manifest": _manifest(manifest),
    }
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
