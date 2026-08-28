"""Resolve a probe root across the repo and the runtime workspace.

After the source/workspace separation (spec §6.1), bulk probes live in
the runtime workspace and leave a ``ProbeRelocationAnchor@1`` in
``probes/``. Tools resolve a project id by checking the repo first,
then the anchor's ``moved_to``, then the workspace convention path.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_PROJECTS = Path(
    r"D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027"
    r"\V4_RUNTIME\workspace\projects"
)


def resolve_probe_root(project_id: str) -> Path:
    repo_probe = ROOT / "probes" / project_id
    if (repo_probe / "project.json").exists():
        return repo_probe
    anchor_path = ROOT / "probes" / f"{project_id}.anchor.json"
    if anchor_path.exists():
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        moved = Path(anchor["moved_to"])
        if (moved / "project.json").exists():
            return moved
        raise FileNotFoundError(
            f"{project_id}: anchor points to missing {moved}"
        )
    workspace_probe = WORKSPACE_PROJECTS / project_id
    if (workspace_probe / "project.json").exists():
        return workspace_probe
    raise FileNotFoundError(
        f"probe {project_id!r} not found in repo, anchors, or workspace"
    )
