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
from archflow.project.repository import FilesystemProjectRepository
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
_NEW_ARCHIVE = "Give the full path of a new .zip file outside the project folder."
_EXISTING_ARCHIVE = "Give the full path of an existing .zip archive."
_PROJECT_FOLDER = "Give the full path of the project folder to export."
_TARGET_PARENT = "Give the full path of the folder to restore into."


def _refusal(exc: ArchiveError, detail: str | None = None) -> HubFailure:
    """The archive layer's own refusal, as this API's status and code."""

    return HubFailure(_ARCHIVE_STATUS.get(exc.code, 422), exc.code, detail or str(exc))


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
    existed = target.exists()
    try:
        repository, summary = restore_project_archive(target, archive_path)
    except ArchiveError as exc:
        detail = str(exc)
        if exc.code == "ARCHIVE_INVALID" and not existed and target.exists():
            # The archive failed its own verification after the folder existed.
            # Hub never deletes a folder, so say which one is now in the way.
            detail = f"{detail}. Remove {target} before retrying."
        raise _refusal(exc, detail) from exc
    project_id, project_dir = _project(str(repository.layout.root))
    version, stage = _position(project_dir)
    return ProjectArchiveRestoreResult(
        summary=summary_dto(summary),
        project=ChatProject(projectId=project_id, projectDir=project_dir, name=project_id,
                            chatCount=0, version=version, stage=stage),
    )
