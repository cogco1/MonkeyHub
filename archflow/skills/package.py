"""Explicit-root, content-addressed loading for provider-neutral Skills."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from archflow.skills.contracts import (
    SkillContractError,
    SkillSpec,
    canonical_json,
)


MANIFEST_NAME = "skill.json"
_MAX_MANIFEST_BYTES = 256_000
_MAX_CONTENT_BYTES = 2_000_000
_INSTANCE_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:project_id|building_name|dimensions|rooms|topology|"
    r"palette|coordinates|precedent_answer|expert_order)\s*[:=]"
)
_COORDINATE_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:x|y|z)\s*[:=]\s*-?[0-9]+(?:\.[0-9]+)?\s*$"
)


class SkillPackageError(SkillContractError):
    """A Skill package is missing, stale, ambiguous, or unsafe to load."""


@dataclass(frozen=True, slots=True)
class SkillContent:
    relative_path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """One verified package; ``root`` is loader input, never stable identity."""

    root: Path
    spec: SkillSpec
    contents: tuple[SkillContent, ...]

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise SkillPackageError("Skill root must be absolute after loading")
        paths = tuple(item.relative_path for item in self.contents)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise SkillPackageError("Skill contents must be unique and sorted")
        if paths != tuple(sorted(self.spec.content_entrypoints)):
            raise SkillPackageError("loaded content differs from SkillSpec")

    @property
    def instructions(self) -> str:
        return self.text(self.spec.instruction_entrypoint)

    @property
    def references(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (path, self.text(path))
            for path in sorted(self.spec.reference_entrypoints)
        )

    def bytes(self, relative_path: str) -> bytes:
        for item in self.contents:
            if item.relative_path == relative_path:
                return item.content
        raise KeyError(relative_path)

    def text(self, relative_path: str) -> str:
        try:
            return self.bytes(relative_path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillPackageError(
                f"Skill text entrypoint is not UTF-8: {relative_path}"
            ) from exc


def _manifest_payload(root: Path) -> Mapping[str, object]:
    manifest = root / MANIFEST_NAME
    if not manifest.is_file() or manifest.is_symlink():
        raise SkillPackageError(f"missing regular {MANIFEST_NAME}")
    raw = manifest.read_bytes()
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise SkillPackageError("Skill manifest exceeds bounded size")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillPackageError("Skill manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise SkillPackageError("Skill manifest must be an object")
    return payload


def _safe_entrypoint(root: Path, value: str) -> tuple[str, Path]:
    if "\\" in value:
        raise SkillPackageError("Skill entrypoints must use POSIX separators")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SkillPackageError(f"unsafe Skill entrypoint: {value!r}")
    normalized = relative.as_posix()
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SkillPackageError(
                f"Skill entrypoint may not cross a symlink: {normalized}"
            )
    if not candidate.is_file():
        raise SkillPackageError(f"missing Skill content: {normalized}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise SkillPackageError(
            f"Skill entrypoint crosses package root: {normalized}"
        )
    return (normalized, resolved)


def _read_contents(root: Path, spec: SkillSpec) -> tuple[SkillContent, ...]:
    loaded: list[SkillContent] = []
    total = 0
    for entrypoint in spec.content_entrypoints:
        normalized, path = _safe_entrypoint(root, entrypoint)
        content = path.read_bytes()
        total += len(content)
        if total > _MAX_CONTENT_BYTES:
            raise SkillPackageError("Skill package content exceeds bounded size")
        loaded.append(SkillContent(normalized, content))
    return tuple(sorted(loaded, key=lambda item: item.relative_path))


def _declared_file_set(root: Path) -> set[str]:
    values: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SkillPackageError("Skill package may not contain symlinks")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST_NAME:
                values.add(relative)
    return values


def compute_package_digest(
    spec: SkillSpec,
    contents: Iterable[SkillContent],
) -> str:
    ordered = tuple(sorted(contents, key=lambda item: item.relative_path))
    payload = {
        "spec": spec.digest_payload(),
        "files": [
            {
                "path": item.relative_path,
                "sha256": item.sha256,
                "size_bytes": len(item.content),
            }
            for item in ordered
        ],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def scan_instance_answer_markers(package: SkillPackage) -> tuple[str, ...]:
    """Return deterministic policy findings for instance-answer assignments."""

    findings: list[str] = []
    for item in package.contents:
        try:
            text = item.content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _INSTANCE_ASSIGNMENT.search(text):
            findings.append(
                f"{item.relative_path}: project-instance assignment is forbidden"
            )
        if _COORDINATE_ASSIGNMENT.search(text):
            findings.append(
                f"{item.relative_path}: fixed coordinate assignment is forbidden"
            )
    return tuple(findings)


def load_skill_package(root: str | Path) -> SkillPackage:
    explicit = Path(root)
    if not explicit.is_absolute():
        raise SkillPackageError("Skill root must be explicit and absolute")
    if not explicit.is_dir() or explicit.is_symlink():
        raise SkillPackageError("Skill root must be a regular directory")
    resolved_root = explicit.resolve(strict=True)
    spec = SkillSpec.from_dict(_manifest_payload(resolved_root))
    contents = _read_contents(resolved_root, spec)
    declared = set(spec.content_entrypoints)
    actual = _declared_file_set(resolved_root)
    if actual != declared:
        raise SkillPackageError(
            "Skill package contains missing or undeclared content: "
            f"missing={sorted(declared - actual)}, "
            f"undeclared={sorted(actual - declared)}"
        )
    expected_digest = compute_package_digest(spec, contents)
    if spec.package_digest != expected_digest:
        raise SkillPackageError(
            "Skill package digest mismatch; content is stale or tampered"
        )
    package = SkillPackage(resolved_root, spec, contents)
    findings = scan_instance_answer_markers(package)
    if findings:
        raise SkillPackageError("; ".join(findings))
    # Text entrypoints fail closed before the package is returned.
    package.instructions
    package.references
    return package


def load_skill_packages(roots: Iterable[str | Path]) -> tuple[SkillPackage, ...]:
    explicit_roots = tuple(roots)
    if not explicit_roots:
        raise SkillPackageError(
            "at least one explicit Skill root is required; fallback is forbidden"
        )
    packages = tuple(load_skill_package(root) for root in explicit_roots)
    identities = tuple(item.spec.identity for item in packages)
    if len(identities) != len(set(identities)):
        raise SkillPackageError("duplicate Skill id/version across explicit roots")
    return tuple(
        sorted(
            packages,
            key=lambda item: (
                item.spec.skill_id,
                item.spec.version,
                item.spec.package_digest,
            ),
        )
    )


def sealed_manifest_payload(root: str | Path) -> dict[str, object]:
    """Return a sealed manifest payload without writing to the package root."""

    explicit = Path(root)
    if not explicit.is_absolute() or not explicit.is_dir():
        raise SkillPackageError("Skill root must be an explicit directory")
    resolved_root = explicit.resolve(strict=True)
    spec = SkillSpec.from_dict(_manifest_payload(resolved_root))
    contents = _read_contents(resolved_root, spec)
    actual = _declared_file_set(resolved_root)
    if actual != set(spec.content_entrypoints):
        raise SkillPackageError("cannot seal missing or undeclared Skill content")
    return spec.with_package_digest(
        compute_package_digest(spec, contents)
    ).to_dict()
