"""Export and restore one portable project archive over the Hub's HTTP surface.

Every byte is written by ``archflow.project.archive``, which installs it
through P036's immutable writer. This module chooses no location of its own:
it normalizes the locations the request named, hands them to that module, and
states the refusal or the summary that came back in the Hub's own wire shapes.
"""

from __future__ import annotations

from pathlib import Path

from archflow.project.archive import (
    ArchiveError,
    ArchiveSummary,
    archive_target,
    restore_project_archive,
    write_project_archive,
)
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectHeadLocked,
    ProjectRepositoryError,
)
from archflow_studio_api.settings import read_application_settings

from .chat import _position, _project, _workspace
from .models import (
    ChatProject,
    HubFailure,
    ProjectArchiveExportRequest,
    ProjectArchiveRestoreRequest,
    ProjectArchiveRestoreResult,
    ProjectArchiveSummary,
)

_ARCHIVE_STATUS = {
    "PROJECT_NOT_FOUND": 404,
    "ARCHIVE_PATH_INVALID": 422,
    "ARCHIVE_INVALID": 422,
    "ARCHIVE_TARGET_OCCUPIED": 409,
}
# P036 refuses an export for two kinds of reason, and only one of them is
# worth asking again. A retained digest that no longer matches the manifest
# this export just built, and a record the operating system would not let it
# read while another process replaced that file, both mean the project moved
# under the read: nothing was installed, and the same export can simply be
# repeated. Every other refusal — a foreign reference, a dependency no run
# holds, a file no retained record names, a restored project that does not
# verify — is a defect of the project itself that repeating only repeats.
_MOVED_UNDER_THE_READ = ("TRANSFER_DIGEST_MISMATCH", "cannot read project record")
_NEW_ARCHIVE = "Give the full path of a new .zip file outside the project folder."
_EXISTING_ARCHIVE = "Give the full path of an existing .zip archive."
_PROJECT_FOLDER = "Give the full path of the project folder to export."
_TARGET_PARENT = "Give the full path of the folder to restore into."


def _refusal(exc: ArchiveError, detail: str | None = None) -> HubFailure:
    """The archive layer's own refusal, as this API's status and code."""

    return HubFailure(_ARCHIVE_STATUS.get(exc.code, 422), exc.code, detail or str(exc))


def _export_refusal(exc: Exception) -> HubFailure:
    """One refusal P036 raised while reading the project this export names.

    ``ProjectIntegrityError`` and its siblings are plain ``RuntimeError``s
    carrying their own sentence, so the sentence is what tells a transient
    conflict from a project this Hub cannot export at all. The caller is told
    which of the two it is, and never told to retry a refusal that stands.

    ``ProjectHeadLocked`` is the one that is known by its type instead: every
    export reads HEAD and the design branches through the Windows sharing
    retry, and a head another process kept busy for that whole retry is the
    plainest case of the project moving under the read.
    """

    if isinstance(exc, ProjectHeadLocked) or str(exc).startswith(_MOVED_UNDER_THE_READ):
        return HubFailure(409, "ARCHIVE_SOURCE_CHANGED",
                          "The project changed while the archive was being read. "
                          "Retry the export.")
    return HubFailure(422, "ARCHIVE_SOURCE_INVALID", str(exc))


def _unusable_path(exc: OSError) -> HubFailure:
    """A location the operating system itself would not let the archive use.

    A vanished folder, a missing drive, a name Windows refuses, a parent that
    is a file. It is the caller's path that is wrong, not this Hub's own
    settings or logs, so it is answered here instead of as a local IO failure.
    """

    return HubFailure(422, "ARCHIVE_PATH_INVALID",
                      f"The archive or target path could not be used: {exc.strerror or exc}.")


def _folder(value: str, detail: str) -> Path:
    """One absolute location the request named, refused rather than guessed."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HubFailure(422, "ARCHIVE_PATH_INVALID", detail)
    return path


def _archive_file(value: str, detail: str) -> Path:
    path = _folder(value, detail)
    if path.suffix.lower() != ".zip":
        raise HubFailure(422, "ARCHIVE_PATH_INVALID", detail)
    return path


def _holds_files(target: Path) -> bool:
    """Whether a failed restore left anything behind in that folder."""

    try:
        return target.is_dir() and any(target.iterdir())
    except OSError:
        return False


def summary_dto(summary: ArchiveSummary) -> ProjectArchiveSummary:
    """The archive layer's summary as the Hub states it, adding nothing."""

    return ProjectArchiveSummary(
        projectId=summary.project_id,
        formatVersion=summary.format_version,
        version=summary.version,
        stateSha256=summary.state_sha256,
        runCount=summary.run_count,
        fileCount=summary.file_count,
        retainedBytes=summary.retained_bytes,
        categories=dict(summary.categories),
        omissions=list(summary.omissions),
        externalDependencies=list(summary.external_dependencies),
        archivePath=summary.archive_path,
        archiveBytes=summary.archive_bytes,
        archiveSha256=summary.archive_sha256,
        verified=summary.verified,
        projectDir=summary.project_dir,
    )


def export_archive(request: ProjectArchiveExportRequest) -> ProjectArchiveSummary:
    """Write the retained snapshot of one existing project to that archive file."""

    project_dir = _folder(request.projectDir, _PROJECT_FOLDER)
    archive_path = _archive_file(request.archivePath, _NEW_ARCHIVE)
    try:
        repository = FilesystemProjectRepository.open(project_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HubFailure(404, "PROJECT_NOT_FOUND",
                         "The selected folder does not contain a valid project manifest.") from exc
    try:
        return summary_dto(write_project_archive(repository, archive_path))
    except ArchiveError as exc:
        raise _refusal(exc) from exc
    except OSError as exc:
        raise _unusable_path(exc) from exc
    except RuntimeError as exc:
        raise _export_refusal(exc) from exc


def restore_archive(
    request: ProjectArchiveRestoreRequest, *, runtime_root: Path,
) -> ProjectArchiveRestoreResult:
    """Install one archive beside the projects this Hub already lists.

    The archive names its own folder, so the request chooses the parent only.
    Nothing is read out of the archive before the restore has succeeded: its
    manifest validation does not cover every key a crafted ZIP could carry.
    """

    archive_path = _archive_file(request.archivePath, _EXISTING_ARCHIVE)
    parent = (_folder(request.targetParent, _TARGET_PARENT) if request.targetParent
              else _workspace(runtime_root, read_application_settings(runtime_root)))
    try:
        target = archive_target(parent, archive_path)
    except ArchiveError as exc:
        raise _refusal(exc) from exc
    except OSError as exc:
        raise HubFailure(422, "ARCHIVE_PATH_INVALID", _EXISTING_ARCHIVE) from exc
    except Exception as exc:
        # The manifest check covers the rows it validates, not every key a
        # crafted ZIP could leave out. Whatever else reading one raises is
        # that archive's problem, not a Hub fault the caller cannot act on.
        raise HubFailure(422, "ARCHIVE_INVALID",
                         "This file could not be read as a project archive.") from exc
    try:
        repository, summary = restore_project_archive(target, archive_path)
    except ArchiveError as exc:
        detail = str(exc)
        if exc.code == "ARCHIVE_INVALID" and _holds_files(target):
            # The archive failed its own verification with bytes already in the
            # folder. Hub deletes nothing, so say which folder is in the way —
            # whether the restore created it or found it empty and filled it.
            detail = f"{detail}. Remove {target} before retrying."
        raise _refusal(exc, detail) from exc
    except OSError as exc:
        raise _unusable_path(exc) from exc
    except ProjectRepositoryError as exc:
        # The archive layer states the repository refusals it knows how to
        # name, not the head lock a second restore of the same project id
        # holds while it writes that very folder. This call installed nothing;
        # what is in the way is the other restore, so it is the same conflict
        # an occupied target already is.
        raise HubFailure(409, "ARCHIVE_TARGET_OCCUPIED", str(exc)) from exc
    project_id, project_dir = _project(str(repository.layout.root))
    version, stage = _position(project_dir)
    return ProjectArchiveRestoreResult(
        summary=summary_dto(summary),
        project=ChatProject(projectId=project_id, projectDir=project_dir, name=project_id,
                            chatCount=0, version=version, stage=stage),
    )
