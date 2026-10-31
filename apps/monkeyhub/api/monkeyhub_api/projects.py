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

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_CANDIDATE_DELTA
from archflow.project.refs import ProjectRecordRef, require_identifier
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord

from .models import HubFailure, plain_spelling


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
    return repository.layout.project_id, plain_spelling(str(repository.layout.root))


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


#: How much of a step's name the project list keeps: it is one small line under the project's name.
STEP_NAME_LIMIT = 48
# A project's current step, kept under the stats of the files it is read from (the working position, HEAD and the
# design line); read again only once one of them moved.
_STEPS: dict[str, tuple[tuple, str | None]] = {}
_STEP_FILES = ("design/working.json", "HEAD", "design/branches.json")


def _step_stamp(root: str) -> tuple | None:
    stamp = []
    for name in _STEP_FILES:
        try:
            stat = os.stat(Path(root, name))
        except FileNotFoundError:
            stamp.append(None)
        except OSError:
            return None
        else:
            stamp.append((stat.st_size, stat.st_mtime_ns, stat.st_ino))
    return tuple(stamp)


def _short(words: object) -> str | None:
    text = " ".join(words.split()) if isinstance(words, str) else ""
    if not text:
        return None
    return text if len(text) <= STEP_NAME_LIMIT else text[:STEP_NAME_LIMIT - 1].rstrip() + "…"


def _read_step(root: str) -> str | None:
    """The name of the step the working design stands on, in the words the history page names it by.

    The name a person or the AI gave that step, else the label of the Stage accepted on it, else the words its
    change was asked in. None while the project has no working position or nothing names the step.
    """

    try:
        repository = FilesystemProjectRepository.open(Path(root))
        value, _ = repository.read_working_draft()
        current = value.get("current")
        if not current:
            return None
        named = _short(value["runs"][current].get("label"))
        if named:
            return named
        for branch in repository.read_design_branches().values():
            stage = repository.load_json(ProjectRecordRef.from_dict(branch["head_stage"]))
            if stage.get("candidate_id") == current and _short(stage.get("label")):
                return _short(stage["label"])
        run = repository.load_run(current)
        for ref in repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=current)):
            if ref.record_kind == STUDIO_CANDIDATE_DELTA:
                return _short(repository.load_json(ref).get("request"))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return None
    return None


def current_step(root: str) -> str | None:
    """``_read_step``, kept under the stats of the files it reads."""

    stamp = _step_stamp(root)
    kept = _STEPS.get(root)
    if stamp is not None and kept is not None and kept[0] == stamp:
        return kept[1]
    step = _read_step(root)
    if stamp is not None and stamp == _step_stamp(root):
        _STEPS[root] = (stamp, step)
    return step


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


#: What a folder conversation's projectId starts with. A project id never holds a colon (``require_identifier``),
#: so a folder can never be taken for a project, nor its grants for a project's.
FOLDER_PREFIX = "folder:"


def is_folder_id(project_id: str | None) -> bool:
    return isinstance(project_id, str) and project_id.startswith(FOLDER_PREFIX)


def _folder(path: str) -> tuple[str, str]:
    """A plain folder a conversation works in: its id (``folder:`` and a digest of its key) and its spelling.

    Any existing absolute folder except a whole drive and a MonkeyHub project,
    whose files change only through the Hub (ADR-012): that one is opened as a
    project instead.
    """
    from hashlib import sha256

    from .runtime.workers import project_key

    root = Path(path)
    if not root.is_absolute() or not root.is_dir():
        raise HubFailure(422, "CHAT_FOLDER_INVALID", "Choose an existing absolute folder.")
    resolved = root.resolve()
    if resolved.parent == resolved:
        raise HubFailure(422, "CHAT_FOLDER_INVALID", "Choose a folder inside the drive, not the whole drive.")
    try:
        FilesystemProjectRepository.open(resolved).read_head()
    except (OSError, ValueError, RuntimeError):
        if (resolved / "project.json").exists():
            # A project that cannot be read is still a project: never a folder an agent may write into.
            raise HubFailure(409, "CHAT_FOLDER_IS_PROJECT", "This folder holds a MonkeyHub project that could not be "
                             "read; repair or restore it rather than opening it as a plain folder.") from None
    else:
        raise HubFailure(409, "CHAT_FOLDER_IS_PROJECT",
                         "This folder is a MonkeyHub project; open it as a project, whose files change only through the Hub.")
    spelling = plain_spelling(str(resolved))
    return FOLDER_PREFIX + sha256(project_key(spelling).encode("utf-8")).hexdigest()[:16], spelling


def _bound(project_id: str, project_dir: str) -> tuple[str, str]:
    """What a conversation is bound to, read again: ``_project`` for a project, ``_folder`` for a folder."""
    return _folder(project_dir) if is_folder_id(project_id) else _project(project_dir)
