"""The Hub's directory of projects, and the identifiers it accepts.

A project is a folder P036 can open: its identity, published version and
accepted Stage are read through the project's own interfaces, and a project the
Hub creates gets the same empty initialization the project tool gives. The
listed projects' answers are kept until their files move. A chat, message or
attachment id is a canonical UUID or refused as not found.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from archflow.project.refs import ProjectRecordRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord

from .models import HubFailure


def _identifier(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.") from exc
    if parsed != value:
        raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
    return parsed


def _project(path: str) -> tuple[str, str]:
    root = Path(path)
    if not root.is_absolute():
        raise HubFailure(422, "CHAT_PROJECT_INVALID", "Choose an existing absolute project folder.")
    try:
        repository = FilesystemProjectRepository.open(root)
        repository.read_head()
    except (OSError, ValueError, RuntimeError) as exc:
        raise HubFailure(422, "CHAT_PROJECT_INVALID", "The selected folder is not a readable ArchFlow project.") from exc
    return repository.layout.project_id, str(repository.layout.root)


def _position(root: str) -> tuple[int | None, str | None]:
    """The published version and accepted Stage this project itself holds.

    Read through the project's existing P036 interfaces. A failure to read is
    reported as "not known" rather than as a version, and a candidate run is
    never one of these answers.
    """
    try:
        repository = FilesystemProjectRepository.open(Path(root))
        version = repository.read_head().version
        branches = repository.read_design_branches()
    except (OSError, ValueError, RuntimeError):
        return None, None
    branch = branches.get("main") or next(iter(branches.values()), None)
    if branch is None:
        return version, None
    try:
        stage = repository.load_json(ProjectRecordRef.from_dict(branch["head_stage"]))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return version, None
    label = stage.get("label")
    return version, label if isinstance(label, str) and label else None


# A listed project's identity and position, kept under the stats of the three
# files they are read from (#449). Opening a project verifies all of it, and
# the project list is read on every Hub refresh; a replaced or rewritten
# manifest, HEAD or design line has another stat and is read again.
_LISTED: dict[tuple[str, bool], tuple[tuple, tuple]] = {}
_LISTED_FILES = ("project.json", "HEAD", "design/branches.json")


def _listed_stamp(root: str) -> tuple | None:
    stamp = []
    for name in _LISTED_FILES:
        try:
            stat = os.stat(Path(root, name))
        except FileNotFoundError:
            stamp.append(None)
        except OSError:
            return None
        else:
            stamp.append((stat.st_size, stat.st_mtime_ns, stat.st_ino))
    return tuple(stamp)


def _listed_project(root: str, *, identify: bool = False) -> tuple[tuple[str, str] | None, tuple[int | None, str | None]]:
    """``_project`` (when ``identify``) and ``_position`` of one listed project, read again only once its files moved."""

    key = (root, identify)
    stamp = _listed_stamp(root)
    kept = _LISTED.get(key)
    if stamp is not None and kept is not None and kept[0] == stamp:
        return kept[1]
    identity = _project(root) if identify else None
    answer = (identity, _position(identity[1] if identity else root))
    # A position that could not be read is not kept: it is asked again next time.
    if stamp is not None and answer[1][0] is not None and stamp == _listed_stamp(root):
        _LISTED[key] = (stamp, answer)
    return answer


def _workspace(runtime_root: Path, settings) -> Path:
    """Where new projects are created for this Hub run.

    The explicitly saved location, else the folder the currently chosen project
    already lives in, else a `workspace/projects` folder under this Hub's own
    explicitly supplied runtime root. It is a location only; every project write
    still goes through P036.
    """
    if settings.workspace_dir:
        return Path(settings.workspace_dir)
    if settings.project_dir:
        parent = Path(settings.project_dir).parent
        if parent != Path(settings.project_dir):
            return parent
    return runtime_root / "workspace" / "projects"


def _new_project(workspace: Path, name: str) -> Path:
    """Create one empty P036 project in that workspace, as the tool entry does.

    The same initialization `tools/project/create_project.py` performs: a project at
    version 0 with an empty authored record, no run and no design content. A
    name that is not a project id, an escape out of the workspace, or a folder
    that already holds anything is refused with its own reason.
    """
    chosen = name.strip()
    if not chosen:
        raise HubFailure(422, "PROJECT_NAME_REQUIRED", "Enter a name for the new project.")
    try:
        require_identifier(chosen, "project_id")
    except (ValueError, TypeError) as exc:
        raise HubFailure(422, "PROJECT_NAME_INVALID",
                         "Use letters, digits, hyphens or underscores for the project name.") from exc
    if not workspace.is_absolute():
        raise HubFailure(422, "WORKSPACE_INVALID", "Choose an absolute workspace folder first.")
    root = (workspace / chosen).resolve()
    if root.parent != workspace.resolve():
        raise HubFailure(422, "PROJECT_NAME_INVALID", "The project name cannot name another folder.")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise HubFailure(409, "PROJECT_EXISTS",
                         "A folder of that name already exists here and was left untouched.")
    try:
        # P036 creates the project root itself; Hub chooses no path of its own
        # and writes nothing beside it.
        record = StateRecord(project_id=chosen, run_id="authored", entities=())
        repository = FilesystemProjectRepository.initialize(
            root, project_id=chosen,
            initial_state={"project_id": chosen, "version": 0},
            authored_record=record.to_dict(),
        )
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise HubFailure(422, "PROJECT_CREATE_FAILED",
                         f"The project could not be created here: {exc}") from exc
    return repository.layout.root
