"""Read-only reference map used before a retained project-format migration.

Format 1 -> 2 changes the meaning of ``ProjectVersionRef.state_sha256`` from a
snapshot blob digest to the semantic state digest.  Rewriting only project.json
or HEAD would therefore strand retained runs/records on the old identity.

This module performs the missing read-only analysis step: over the exact
transfer closure already verified by P036, find every value that is *exactly*
a ProjectVersionRef and report its file and JSON location.  It never guesses
record references (which share shapes with artifact references) and it never
writes project data.  The eventual migrator can require this map to be fully
accounted for by typed owners before any byte is replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archflow.project.repository import (
    CURRENT_FORMAT_VERSION,
    LEGACY_FORMAT_VERSION,
    FilesystemProjectRepository,
    ProjectIntegrityError,
    plan_project_migration,
)


_VERSION_KEYS = frozenset({"project_id", "version", "state_sha256"})


@dataclass(frozen=True, slots=True)
class RetainedVersionReference:
    file: str
    json_path: str
    project_id: str
    version: int
    state_sha256: str


@dataclass(frozen=True, slots=True)
class ProjectVersionReferenceReport:
    project_id: str
    source_format_version: int
    target_format_version: int
    references: tuple[RetainedVersionReference, ...]
    scanned_json_files: int


def _json_pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def collect_project_version_references(
    value: Any,
    *,
    file: str,
    path: str = "",
) -> tuple[RetainedVersionReference, ...]:
    """Find exact ProjectVersionRef-shaped values without inferring semantics."""

    found: list[RetainedVersionReference] = []
    if isinstance(value, Mapping):
        if frozenset(value.keys()) == _VERSION_KEYS:
            project_id = value.get("project_id")
            version = value.get("version")
            digest = value.get("state_sha256")
            if (
                isinstance(project_id, str)
                and project_id
                and isinstance(version, int)
                and not isinstance(version, bool)
                and version >= 0
                and isinstance(digest, str)
                and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest)
            ):
                found.append(
                    RetainedVersionReference(
                        file=file,
                        json_path=path or "/",
                        project_id=project_id,
                        version=version,
                        state_sha256=digest,
                    )
                )
                return tuple(found)
        for key, item in value.items():
            found.extend(
                collect_project_version_references(
                    item,
                    file=file,
                    path=f"{path}/{_json_pointer_token(key)}",
                )
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(
                collect_project_version_references(
                    item,
                    file=file,
                    path=f"{path}/{index}",
                )
            )
    return tuple(found)


def analyze_project_version_references(
    root: Path,
    *,
    target_format_version: int = CURRENT_FORMAT_VERSION,
) -> ProjectVersionReferenceReport:
    """Map exact version refs in one verified legacy retained closure.

    The existing migration planner remains the gate.  If it cannot inventory
    the whole project without mutation, this analysis refuses too.  Unknown
    retained record kinds remain blockers in the planner; this function does
    not turn shape recognition into a claim that their payload is understood.
    """

    plan = plan_project_migration(root, target_format_version=target_format_version)
    if not plan.planned:
        detail = "; ".join(plan.blockers) or "project could not be planned"
        raise ProjectIntegrityError(f"MIGRATION_REFERENCE_SCAN_REFUSED: {detail}")
    source = plan.inspection.format_version
    if source != LEGACY_FORMAT_VERSION or target_format_version != CURRENT_FORMAT_VERSION:
        raise ProjectIntegrityError(
            "MIGRATION_REFERENCE_SCAN_NOT_APPLICABLE: expected format 1 -> 2"
        )
    if plan.project_id is None:
        raise ProjectIntegrityError("MIGRATION_REFERENCE_SCAN_REFUSED: project identity missing")

    repository = FilesystemProjectRepository.open(root)
    transfer = repository.export_transfer(include_contents=False, include_all_runs=True)
    references: list[RetainedVersionReference] = []
    scanned = 0
    for entry in transfer["files"]:
        file = entry["path"]
        if not (file.endswith(".json") or file == "HEAD"):
            continue
        data = repository.read_transfer_file(file, entry["sha256"])
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectIntegrityError(
                f"MIGRATION_REFERENCE_SCAN_INVALID_JSON: {file}"
            ) from exc
        scanned += 1
        for ref in collect_project_version_references(payload, file=file):
            if ref.project_id != plan.project_id:
                raise ProjectIntegrityError(
                    f"MIGRATION_REFERENCE_SCAN_FOREIGN_PROJECT: {file}{ref.json_path}"
                )
            references.append(ref)

    return ProjectVersionReferenceReport(
        project_id=plan.project_id,
        source_format_version=source,
        target_format_version=target_format_version,
        references=tuple(sorted(references, key=lambda item: (item.file, item.json_path))),
        scanned_json_files=scanned,
    )
