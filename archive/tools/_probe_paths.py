"""Resolve a probe root across the repo and the runtime workspace.

After the source/workspace separation (spec §6.1), bulk probes live in
the runtime workspace and leave a ``ProbeRelocationAnchor@1`` in
``probes/``. Tools resolve a project id by checking the repo first,
then the anchor's ``moved_to``, then the workspace convention path.
"""

from __future__ import annotations

from pathlib import Path

from archflow.project.location import locate_project

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_PROJECTS = Path(
    r"D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027"
    r"\V4_RUNTIME\workspace\projects"
)


def resolve_probe_root(project_id: str) -> Path:
    """Host-configured compatibility adapter over archflow.project."""

    return locate_project(
        project_id,
        local_projects_root=ROOT / "probes",
        relocation_anchor_root=ROOT / "probes",
        workspace_projects_root=WORKSPACE_PROJECTS,
    ).root
