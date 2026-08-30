"""Read-only discovery of one P036 project repository.

Project identity remains the portable ``project_id``.  A caller supplies the
configured local and runtime roots; this module resolves the current physical
location, validates relocation anchors and immutable ``project.json`` identity,
and returns a typed location.  It never creates directories or writes state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from archflow.project.manifest import ProjectManifest, ProjectManifestError
from archflow.project.refs import require_identifier


class ProjectLocationError(ValueError):
    """A configured project location is missing, corrupt, or cross-project."""


class ProjectLocationKind(StrEnum):
    LOCAL = "local"
    RELOCATED = "relocated"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class ProjectLocation:
    """Resolved physical location without canonical-state authority."""

    project_id: str
    root: Path
    kind: ProjectLocationKind
    anchor_path: Path | None = None

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        if not isinstance(self.root, Path):
            raise TypeError("root must be a Path")
        if not isinstance(self.kind, ProjectLocationKind):
            raise TypeError("kind must be ProjectLocationKind")
        object.__setattr__(self, "root", self.root.resolve(strict=False))
        if self.anchor_path is not None:
            if not isinstance(self.anchor_path, Path):
                raise TypeError("anchor_path must be a Path or None")
            object.__setattr__(
                self,
                "anchor_path",
                self.anchor_path.resolve(strict=False),
            )
        if (self.kind is ProjectLocationKind.RELOCATED) != (
            self.anchor_path is not None
        ):
            raise ProjectLocationError(
                "only relocated locations may carry an anchor_path"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProjectLocation@1",
            "project_id": self.project_id,
            "kind": self.kind.value,
            "root": self.root.as_posix(),
            "anchor_path": (
                self.anchor_path.as_posix()
                if self.anchor_path is not None
                else None
            ),
            "canonical_write_authority": False,
        }


def _root(value: object, field: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{field} must be a Path")
    return value.resolve(strict=False)


def _manifest(root: Path, project_id: str) -> ProjectManifest:
    path = root / "project.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = ProjectManifest.from_dict(payload)
    except (OSError, json.JSONDecodeError, ProjectManifestError) as exc:
        raise ProjectLocationError(
            f"{project_id}: cannot load an exact project.json at {root}"
        ) from exc
    if manifest.project_id != project_id:
        raise ProjectLocationError(
            f"{project_id}: located manifest belongs to {manifest.project_id}"
        )
    return manifest


def _sha256_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ProjectLocationError(
            f"cannot read relocation target manifest: {path}"
        ) from exc
    return hashlib.sha256(data).hexdigest()


def _relocated(
    anchor_path: Path,
    *,
    project_id: str,
) -> ProjectLocation:
    try:
        payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectLocationError(
            f"{project_id}: relocation anchor is unreadable"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ProjectLocationError(
            f"{project_id}: relocation anchor must be an object"
        )
    if (
        payload.get("schema") != "ProbeRelocationAnchor@1"
        or payload.get("project_id") != project_id
    ):
        raise ProjectLocationError(
            f"{project_id}: relocation anchor identity or schema changed"
        )
    moved_to = payload.get("moved_to")
    if not isinstance(moved_to, str) or not moved_to.strip():
        raise ProjectLocationError(
            f"{project_id}: relocation anchor lacks moved_to"
        )
    root = Path(moved_to)
    if not root.is_absolute():
        raise ProjectLocationError(
            f"{project_id}: relocation target must be absolute host config"
        )
    root = root.resolve(strict=False)
    _manifest(root, project_id)
    expected_manifest_hash = payload.get("project_json_sha256")
    if expected_manifest_hash is not None:
        if (
            not isinstance(expected_manifest_hash, str)
            or len(expected_manifest_hash) != 64
            or any(
                char not in "0123456789abcdef"
                for char in expected_manifest_hash.lower()
            )
        ):
            raise ProjectLocationError(
                f"{project_id}: anchor project_json_sha256 is invalid"
            )
        if _sha256_file(root / "project.json") != (
            expected_manifest_hash.lower()
        ):
            raise ProjectLocationError(
                f"{project_id}: relocation target manifest hash changed"
            )
    return ProjectLocation(
        project_id=project_id,
        root=root,
        kind=ProjectLocationKind.RELOCATED,
        anchor_path=anchor_path,
    )


def locate_project(
    project_id: str,
    *,
    local_projects_root: Path,
    relocation_anchor_root: Path | None = None,
    workspace_projects_root: Path | None = None,
) -> ProjectLocation:
    """Resolve one existing project in deterministic authority order.

    A local P036 project wins first.  A named relocation anchor is next and is
    fail-closed when corrupt or stale.  A caller-configured workspace root is
    the final fallback for active, unpromoted projects.
    """

    require_identifier(project_id, "project_id")
    local_root = _root(local_projects_root, "local_projects_root")
    local = local_root / project_id
    if (local / "project.json").is_file():
        _manifest(local, project_id)
        return ProjectLocation(
            project_id=project_id,
            root=local,
            kind=ProjectLocationKind.LOCAL,
        )

    anchors = (
        _root(relocation_anchor_root, "relocation_anchor_root")
        if relocation_anchor_root is not None
        else local_root
    )
    anchor_path = anchors / f"{project_id}.anchor.json"
    if anchor_path.is_file():
        return _relocated(anchor_path, project_id=project_id)

    if workspace_projects_root is not None:
        workspace_root = _root(
            workspace_projects_root,
            "workspace_projects_root",
        )
        workspace = workspace_root / project_id
        if (workspace / "project.json").is_file():
            _manifest(workspace, project_id)
            return ProjectLocation(
                project_id=project_id,
                root=workspace,
                kind=ProjectLocationKind.WORKSPACE,
            )

    raise ProjectLocationError(
        f"{project_id}: no local project, relocation anchor, or configured "
        "workspace project exists"
    )


def open_located_project(
    project_id: str,
    *,
    local_projects_root: Path,
    relocation_anchor_root: Path | None = None,
    workspace_projects_root: Path | None = None,
):
    """Locate and open the single P036 repository for ``project_id``."""

    from archflow.project.repository import FilesystemProjectRepository

    location = locate_project(
        project_id,
        local_projects_root=local_projects_root,
        relocation_anchor_root=relocation_anchor_root,
        workspace_projects_root=workspace_projects_root,
    )
    return location, FilesystemProjectRepository.open(location.root)
