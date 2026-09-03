"""Thin host-surface translations generated from one verified Skill package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from archive.archflow.skills.contracts import SkillContractError, digest_json
from archive.archflow.skills.package import SkillPackage


class SkillSurfaceError(SkillContractError):
    """A host-surface profile or generated artifact is invalid."""


@dataclass(frozen=True, slots=True)
class SkillSurfaceProfile:
    surface_id: str
    skill_path_template: str

    def __post_init__(self) -> None:
        if not self.surface_id or not self.surface_id.replace("-", "").isalnum():
            raise SkillSurfaceError("surface_id must be simple text")
        if "{skill_id}" not in self.skill_path_template:
            raise SkillSurfaceError(
                "skill_path_template must contain {skill_id}"
            )

    def skill_root(self, skill_id: str) -> PurePosixPath:
        path = PurePosixPath(
            self.skill_path_template.format(skill_id=skill_id)
        )
        if path.is_absolute() or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise SkillSurfaceError("surface path escapes its export root")
        return path


DEFAULT_SKILL_SURFACES: dict[str, SkillSurfaceProfile] = {
    "codex": SkillSurfaceProfile("codex", "skills/{skill_id}"),
    "claude": SkillSurfaceProfile("claude", ".claude/skills/{skill_id}"),
    "kimi": SkillSurfaceProfile("kimi", ".kimi/skills/{skill_id}"),
}


@dataclass(frozen=True, slots=True)
class SkillSurfaceFile:
    relative_path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class SkillSurfaceArtifact:
    surface_id: str
    skill_id: str
    skill_version: str
    package_digest: str
    contract_digest: str
    files: tuple[SkillSurfaceFile, ...]

    SCHEMA = "SkillSurfaceArtifact@1"

    def __post_init__(self) -> None:
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise SkillSurfaceError("surface files must be unique and sorted")
        if not any(path.endswith("/SKILL.md") for path in paths):
            raise SkillSurfaceError("surface artifact lacks SKILL.md")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "surface_id": self.surface_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "package_digest": self.package_digest,
            "contract_digest": self.contract_digest,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "size_bytes": len(item.content),
                }
                for item in self.files
            ],
            "source_of_truth": "SkillSpec@1",
            "runtime_authority": False,
        }


def _skill_markdown(package: SkillPackage) -> str:
    spec = package.spec
    description = json.dumps(spec.description, ensure_ascii=False)
    outputs = ", ".join(spec.output_schemas)
    body = f"""---
name: {spec.skill_id}
description: {description}
---

# {spec.skill_id}

Use only the detached ArchFlow invocation input supplied by the host adapter.
Return one declared output schema: {outputs}.

- Bind every result to the supplied context digest and current obligation refs.
- Keep advice read-only and proposals exact-base and candidate-only.
- Do not claim verification, acceptance, canonical state, or a world write.
- Do not access transcripts, sibling state, repositories, committers, or live world handles.
- Stay within the declared tool scopes, timeout, output, and retry budgets.

Package identity: `{spec.skill_id}@{spec.version}`

Package digest: `{spec.package_digest}`

## Package instructions

{package.instructions.rstrip()}
"""
    return body.rstrip() + "\n"


def render_skill_surface(
    package: SkillPackage,
    surface: str | SkillSurfaceProfile,
) -> SkillSurfaceArtifact:
    """Render host files in memory; this adapter performs no filesystem write."""

    if not isinstance(package, SkillPackage):
        raise TypeError("package must be a SkillPackage")
    if isinstance(surface, str):
        try:
            profile = DEFAULT_SKILL_SURFACES[surface]
        except KeyError as exc:
            raise SkillSurfaceError(
                "unknown surface; pass an explicit SkillSurfaceProfile"
            ) from exc
    elif isinstance(surface, SkillSurfaceProfile):
        profile = surface
    else:
        raise TypeError("surface must be text or SkillSurfaceProfile")

    skill_root = profile.skill_root(package.spec.skill_id)
    generated = _skill_markdown(package).encode("utf-8")
    files = [
        SkillSurfaceFile(
            (skill_root / "SKILL.md").as_posix(),
            generated,
        )
    ]
    for entrypoint in (
        *package.spec.reference_entrypoints,
        *package.spec.resource_entrypoints,
    ):
        target = (skill_root / PurePosixPath(entrypoint)).as_posix()
        if target.endswith("/SKILL.md"):
            raise SkillSurfaceError(
                "package resource collides with generated SKILL.md"
            )
        files.append(SkillSurfaceFile(target, package.bytes(entrypoint)))
    contract_digest = digest_json(
        {
            "skill_id": package.spec.skill_id,
            "version": package.spec.version,
            "package_digest": package.spec.package_digest,
            "skill_markdown_sha256": hashlib.sha256(generated).hexdigest(),
            "resources": [
                {
                    "path": entrypoint,
                    "sha256": hashlib.sha256(
                        package.bytes(entrypoint)
                    ).hexdigest(),
                }
                for entrypoint in sorted(
                    (
                        *package.spec.reference_entrypoints,
                        *package.spec.resource_entrypoints,
                    )
                )
            ],
        }
    )
    return SkillSurfaceArtifact(
        surface_id=profile.surface_id,
        skill_id=package.spec.skill_id,
        skill_version=package.spec.version,
        package_digest=package.spec.package_digest,
        contract_digest=contract_digest,
        files=tuple(sorted(files, key=lambda item: item.relative_path)),
    )


def validate_codex_skill_markdown(content: str) -> None:
    """Validate the generated subset needed for a Codex-compatible Skill."""

    if not isinstance(content, str):
        raise TypeError("content must be text")
    lines = content.splitlines()
    if len(lines) < 5 or lines[0] != "---":
        raise SkillSurfaceError("SKILL.md lacks YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise SkillSurfaceError("SKILL.md frontmatter is unclosed") from exc
    frontmatter = lines[1:closing]
    keys = tuple(
        line.split(":", 1)[0].strip()
        for line in frontmatter
        if ":" in line
    )
    if keys != ("name", "description"):
        raise SkillSurfaceError(
            "Codex SKILL.md frontmatter must contain only name and description"
        )
    if not lines[1].removeprefix("name:").strip():
        raise SkillSurfaceError("Codex Skill name is empty")
    raw_description = lines[2].removeprefix("description:").strip()
    try:
        description = json.loads(raw_description)
    except json.JSONDecodeError as exc:
        raise SkillSurfaceError("Codex Skill description is invalid") from exc
    if not isinstance(description, str) or not description.strip():
        raise SkillSurfaceError("Codex Skill description is empty")


def codex_skill_file(artifact: SkillSurfaceArtifact) -> SkillSurfaceFile:
    if artifact.surface_id != "codex":
        raise SkillSurfaceError("artifact is not a Codex surface")
    matches = tuple(
        item for item in artifact.files if item.relative_path.endswith("/SKILL.md")
    )
    if len(matches) != 1:
        raise SkillSurfaceError("Codex artifact has ambiguous SKILL.md")
    validate_codex_skill_markdown(matches[0].content.decode("utf-8"))
    return matches[0]
