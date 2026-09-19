"""The portable transport container around the existing P036 transfer.

An archive is one ZIP holding a ProjectArchiveManifest@1 and the retained bytes
that manifest names; it is never a second project format, and the normal
project readers stay the only authority over what a restored project says.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectAlreadyExists,
    ProjectIntegrityError,
    _retained_category,
    _write_immutable,
)

ARCHIVE_SCHEMA = "ProjectArchiveManifest@1"
ARCHIVE_VERSION = 1
ARCHIVE_MANIFEST_PATH = "manifest.json"
ARCHIVE_PROJECT_PREFIX = "project/"
ARCHIVE_OMISSIONS = (
    "credentials/tokens",
    "process/runtime state",
    "runtime caches",
    "rebuildable previews and unbounded telemetry/logs",
    "unreferenced exports: exports/ travels only where a retained record names a file in it",
)
_TRANSFER_KEYS = {
    "project_id",
    "format_version",
    "mode",
    "root_run_id",
    "head",
    "branches",
    "run_ids",
    "files",
    "contents",
}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ArchiveError(ValueError):
    """Archive layer refusal, naming which refusal it is in ``code``.

    ``code`` is one of ARCHIVE_PATH_INVALID, ARCHIVE_INVALID and
    ARCHIVE_TARGET_OCCUPIED. PROJECT_NOT_FOUND is not one of them: opening a
    project is the caller's own step, so the layer that opens it — the CLI or
    the Hub — states that refusal itself. This one stays a ValueError so a
    caller that already refuses on one keeps working, and ``str`` is the
    user-facing sentence alone.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ArchiveSummary:
    """What one archive holds, said without any design content.

    Identities, counts and sizes only: enough to recognise the project, the
    revision and what did not travel, without opening the project itself.
    """

    project_id: str
    format_version: int
    version: int
    state_sha256: str
    run_count: int
    file_count: int
    retained_bytes: int
    categories: dict[str, int]
    omissions: tuple[str, ...]
    external_dependencies: tuple[str, ...]
    archive_path: str
    archive_bytes: int
    archive_sha256: str
    verified: bool
    project_dir: str


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArchiveError(
            "ARCHIVE_INVALID", "archive manifest must contain finite JSON data"
        ) from exc
    return (encoded + "\n").encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _absolute(path: str | Path, what: str) -> Path:
    """Resolve one caller-supplied location, refusing a relative one.

    A relative path silently means the calling process's working directory,
    which is a shell for one caller of this module and a running service's
    install directory for another.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        raise ArchiveError(
            "ARCHIVE_PATH_INVALID", f"{what} must be an absolute path: {candidate}"
        )
    return candidate.resolve()


def archive_manifest(repository: FilesystemProjectRepository) -> dict[str, Any]:
    transfer = repository.export_transfer(include_contents=False, include_all_runs=True)
    if transfer.get("mode") != "snapshot" or transfer.get("root_run_id") is not None:
        raise ArchiveError(
            "ARCHIVE_INVALID",
            "project archive export requires one complete retained snapshot",
        )
    if transfer.get("contents") != {}:
        raise ArchiveError(
            "ARCHIVE_INVALID", "project archive manifest must not inline project bytes"
        )
    return {
        "schema": ARCHIVE_SCHEMA,
        "archive_version": ARCHIVE_VERSION,
        "transfer": transfer,
        "omissions": list(ARCHIVE_OMISSIONS),
    }


def validate_archive_manifest(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "archive_version",
        "transfer",
        "omissions",
    }:
        raise ArchiveError("ARCHIVE_INVALID", "invalid project archive manifest fields")
    if payload.get("schema") != ARCHIVE_SCHEMA or payload.get("archive_version") != ARCHIVE_VERSION:
        raise ArchiveError("ARCHIVE_INVALID", "unsupported project archive manifest")
    transfer = payload.get("transfer")
    if not isinstance(transfer, dict) or set(transfer) != _TRANSFER_KEYS:
        raise ArchiveError("ARCHIVE_INVALID", "invalid project archive transfer metadata")
    if transfer.get("mode") != "snapshot" or transfer.get("root_run_id") is not None:
        raise ArchiveError(
            "ARCHIVE_INVALID", "project archive must contain a complete retained snapshot"
        )
    if transfer.get("contents") != {}:
        raise ArchiveError(
            "ARCHIVE_INVALID", "project archive manifest may not inline project bytes"
        )
    if not isinstance(transfer.get("project_id"), str) or not transfer["project_id"]:
        raise ArchiveError("ARCHIVE_INVALID", "project archive is missing project identity")
    if type(transfer.get("format_version")) is not int or transfer["format_version"] < 1:
        raise ArchiveError("ARCHIVE_INVALID", "project archive format version is invalid")
    if not isinstance(transfer.get("files"), list):
        raise ArchiveError("ARCHIVE_INVALID", "project archive file manifest is invalid")
    omissions = payload.get("omissions")
    if not isinstance(omissions, list) or any(not isinstance(item, str) for item in omissions):
        raise ArchiveError("ARCHIVE_INVALID", "project archive omissions are invalid")
    return payload


def read_project_archive(
    source: str | Path | BinaryIO,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one portable archive and rebuild the existing transfer envelope.

    Nothing is extracted by path. Every member is addressed by the manifest,
    hashed before bootstrap, and then handed to P036's existing transfer
    validator so normal project readers remain the final integrity authority.
    """

    if isinstance(source, (str, Path)):
        source = Path(source).resolve()
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ArchiveError(
                    "ARCHIVE_INVALID", "project archive contains duplicate members"
                )
            if ARCHIVE_MANIFEST_PATH not in names:
                raise ArchiveError(
                    "ARCHIVE_INVALID", "project archive is missing manifest.json"
                )
            try:
                payload = json.loads(archive.read(ARCHIVE_MANIFEST_PATH).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveError(
                    "ARCHIVE_INVALID", "project archive manifest is not valid UTF-8 JSON"
                ) from exc
            manifest = validate_archive_manifest(payload)
            transfer = dict(manifest["transfer"])
            contents: dict[str, str] = {}
            expected_names = {ARCHIVE_MANIFEST_PATH}
            seen_paths: set[str] = set()
            for row in transfer["files"]:
                if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
                    raise ArchiveError(
                        "ARCHIVE_INVALID", "project archive contains an invalid file row"
                    )
                path = row.get("path")
                digest = row.get("sha256")
                size = row.get("size")
                if not isinstance(path, str) or not path or path in seen_paths:
                    raise ArchiveError(
                        "ARCHIVE_INVALID",
                        "project archive file path is invalid or duplicated",
                    )
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ArchiveError(
                        "ARCHIVE_INVALID", f"project archive digest is invalid: {path}"
                    )
                if type(size) is not int or size < 0:
                    raise ArchiveError(
                        "ARCHIVE_INVALID", f"project archive size is invalid: {path}"
                    )
                seen_paths.add(path)
                member = f"{ARCHIVE_PROJECT_PREFIX}{path}"
                expected_names.add(member)
                try:
                    info = archive.getinfo(member)
                except KeyError as exc:
                    raise ArchiveError(
                        "ARCHIVE_INVALID",
                        f"project archive is missing required file: {path}",
                    ) from exc
                if info.is_dir() or info.file_size != size:
                    raise ArchiveError(
                        "ARCHIVE_INVALID", f"project archive size mismatch: {path}"
                    )
                data = archive.read(member)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    raise ArchiveError(
                        "ARCHIVE_INVALID", f"project archive digest mismatch: {path}"
                    )
                contents[path] = base64.b64encode(data).decode("ascii")
            if set(names) != expected_names:
                extras = sorted(set(names) - expected_names)
                raise ArchiveError(
                    "ARCHIVE_INVALID",
                    "project archive contains unlisted members"
                    + (f": {', '.join(extras[:3])}" if extras else ""),
                )
            transfer["contents"] = contents
            return manifest, transfer
    except zipfile.BadZipFile as exc:
        raise ArchiveError(
            "ARCHIVE_INVALID", "project archive is not a valid ZIP file"
        ) from exc


def summarize_archive(
    manifest: Mapping[str, Any],
    *,
    archive_path: Path,
    archive_bytes: int,
    archive_sha256: str,
    verified: bool,
    project_dir: Path,
) -> ArchiveSummary:
    """State what one archive holds, from the manifest it already carries."""

    transfer = manifest["transfer"]
    rows = transfer["files"]
    categories: dict[str, int] = {}
    for row in rows:
        category = _retained_category(row["path"])[0]
        categories[category] = categories.get(category, 0) + 1
    return ArchiveSummary(
        project_id=transfer["project_id"],
        format_version=transfer["format_version"],
        version=transfer["head"]["version"],
        state_sha256=transfer["head"]["state_sha256"],
        run_count=len(transfer["run_ids"]),
        file_count=len(rows),
        retained_bytes=sum(row["size"] for row in rows),
        categories=dict(sorted(categories.items())),
        omissions=tuple(manifest["omissions"]),
        # Nothing this archive needs lives outside it today. The row is stated
        # so a later external dependency has to be named rather than assumed.
        external_dependencies=(),
        archive_path=str(archive_path),
        archive_bytes=archive_bytes,
        archive_sha256=archive_sha256,
        verified=verified,
        project_dir=str(project_dir),
    )


def write_project_archive(
    repository: FilesystemProjectRepository,
    archive_path: Path,
) -> ArchiveSummary:
    """Build, self-verify, then immutably install one noncanonical archive."""

    archive_path = _absolute(archive_path, "the project archive")
    project_root = repository.layout.root.resolve()
    if archive_path.is_relative_to(project_root):
        raise ArchiveError(
            "ARCHIVE_PATH_INVALID",
            "write the project archive outside the project directory",
        )
    if archive_path.exists():
        raise ArchiveError(
            "ARCHIVE_PATH_INVALID", f"archive already exists: {archive_path}"
        )
    manifest = archive_manifest(repository)
    transfer = manifest["transfer"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        archive.writestr(_zip_info(ARCHIVE_MANIFEST_PATH), _json_bytes(manifest))
        for row in transfer["files"]:
            data = repository.read_transfer_file(row["path"], row["sha256"])
            if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ArchiveError(
                    "ARCHIVE_INVALID",
                    f"project file changed during archive export: {row['path']}",
                )
            archive.writestr(_zip_info(f"{ARCHIVE_PROJECT_PREFIX}{row['path']}"), data)
    archive_bytes = buffer.getvalue()
    checked_manifest, checked_transfer = read_project_archive(io.BytesIO(archive_bytes))
    if checked_manifest != manifest or checked_transfer["head"] != transfer["head"]:
        raise ArchiveError(
            "ARCHIVE_INVALID",
            "project archive self-verification disagreed with exported snapshot",
        )
    # P036 remains the only generic filesystem writer. The archive is a
    # noncanonical transport artifact, but even its final byte installation is
    # delegated to the existing immutable writer rather than giving this module
    # a second filesystem-write authority.
    _write_immutable(archive_path, archive_bytes)
    return summarize_archive(
        manifest,
        archive_path=archive_path,
        archive_bytes=len(archive_bytes),
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        verified=True,
        project_dir=project_root,
    )


def archive_target(parent: Path, archive_path: Path) -> Path:
    """Where one archive restores under a caller-chosen parent folder.

    The project folder is named by the manifest, never by the archive file or
    the caller: the existing project binding reads the project id from the
    directory name.
    """

    parent = _absolute(parent, "the restore parent folder")
    _, transfer = read_project_archive(_absolute(archive_path, "the project archive"))
    return parent / transfer["project_id"]


def restore_project_archive(
    root: Path,
    archive_path: Path,
) -> tuple[FilesystemProjectRepository, ArchiveSummary]:
    """Install one archive into an empty directory named by the project id."""

    root = _absolute(root, "the restore directory")
    archive_path = _absolute(archive_path, "the project archive")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ArchiveError(
            "ARCHIVE_TARGET_OCCUPIED", f"restore directory already contains files: {root}"
        )
    manifest, transfer = read_project_archive(archive_path)
    project_id = transfer["project_id"]
    if root.name != project_id:
        raise ArchiveError(
            "ARCHIVE_INVALID",
            f"restore directory name must remain the project id {project_id!r}",
        )
    try:
        repository = FilesystemProjectRepository.bootstrap_transfer(
            root,
            transfer,
            expected_project_id=project_id,
        )
        repository.verify()
    except ProjectAlreadyExists as exc:
        raise ArchiveError("ARCHIVE_TARGET_OCCUPIED", str(exc)) from exc
    except ProjectIntegrityError as exc:
        raise ArchiveError("ARCHIVE_INVALID", str(exc)) from exc
    with archive_path.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return repository, summarize_archive(
        manifest,
        archive_path=archive_path,
        archive_bytes=archive_path.stat().st_size,
        archive_sha256=archive_sha256,
        verified=True,
        project_dir=repository.layout.root,
    )
