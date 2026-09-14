"""Create, export, or restore one external P036 project.

Create a new project::

    python tools/create_project.py --project D:/work/projects/my-project
    python tools/create_project.py --project D:/work/projects/my-project \
        --state-record D:/inputs/state-record.json --seats-file D:/inputs/seats.json

Export a retained project as one portable archive::

    python tools/create_project.py --project D:/work/projects/my-project \
        --export-archive D:/backups/my-project.monkeyhub.zip

Restore that archive into a fresh project directory::

    python tools/create_project.py --project D:/restored/my-project \
        --restore-archive D:/backups/my-project.monkeyhub.zip

The directory name is the project id, as required by Studio's existing binding.
Archive transport wraps the existing P036 transfer closure; it does not create a
second project format or make ZIP contents authoritative over normal readers.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.project.refs import require_identifier
from archflow.state.state_record import StateRecord
from tools.run_project import _seat


ARCHIVE_SCHEMA = "ProjectArchiveManifest@1"
ARCHIVE_VERSION = 1
ARCHIVE_MANIFEST_PATH = "manifest.json"
ARCHIVE_PROJECT_PREFIX = "project/"
ARCHIVE_OMISSIONS = (
    "credentials/tokens",
    "process/runtime state",
    "runtime caches",
    "rebuildable previews and unbounded telemetry/logs",
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
        raise ValueError("archive manifest must contain finite JSON data") from exc
    return (encoded + "\n").encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _archive_manifest(repository: FilesystemProjectRepository) -> dict[str, Any]:
    transfer = repository.export_transfer(include_contents=False)
    if transfer.get("mode") != "snapshot" or transfer.get("root_run_id") is not None:
        raise ValueError("project archive export requires one complete retained snapshot")
    if transfer.get("contents") != {}:
        raise ValueError("project archive manifest must not inline project bytes")
    return {
        "schema": ARCHIVE_SCHEMA,
        "archive_version": ARCHIVE_VERSION,
        "transfer": transfer,
        "omissions": list(ARCHIVE_OMISSIONS),
    }


def _validate_archive_manifest(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "archive_version",
        "transfer",
        "omissions",
    }:
        raise ValueError("invalid project archive manifest fields")
    if payload.get("schema") != ARCHIVE_SCHEMA or payload.get("archive_version") != ARCHIVE_VERSION:
        raise ValueError("unsupported project archive manifest")
    transfer = payload.get("transfer")
    if not isinstance(transfer, dict) or set(transfer) != _TRANSFER_KEYS:
        raise ValueError("invalid project archive transfer metadata")
    if transfer.get("mode") != "snapshot" or transfer.get("root_run_id") is not None:
        raise ValueError("project archive must contain a complete retained snapshot")
    if transfer.get("contents") != {}:
        raise ValueError("project archive manifest may not inline project bytes")
    if not isinstance(transfer.get("project_id"), str) or not transfer["project_id"]:
        raise ValueError("project archive is missing project identity")
    if type(transfer.get("format_version")) is not int or transfer["format_version"] < 1:
        raise ValueError("project archive format version is invalid")
    if not isinstance(transfer.get("files"), list):
        raise ValueError("project archive file manifest is invalid")
    omissions = payload.get("omissions")
    if not isinstance(omissions, list) or any(not isinstance(item, str) for item in omissions):
        raise ValueError("project archive omissions are invalid")
    return payload


def _read_project_archive(archive_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one portable archive and rebuild the existing transfer envelope.

    Nothing is extracted by path. Every member is addressed by the manifest,
    hashed before bootstrap, and then handed to P036's existing transfer
    validator so normal project readers remain the final integrity authority.
    """

    archive_path = Path(archive_path).resolve()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("project archive contains duplicate members")
            if ARCHIVE_MANIFEST_PATH not in names:
                raise ValueError("project archive is missing manifest.json")
            try:
                payload = json.loads(archive.read(ARCHIVE_MANIFEST_PATH).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("project archive manifest is not valid UTF-8 JSON") from exc
            manifest = _validate_archive_manifest(payload)
            transfer = dict(manifest["transfer"])
            contents: dict[str, str] = {}
            expected_names = {ARCHIVE_MANIFEST_PATH}
            seen_paths: set[str] = set()
            for row in transfer["files"]:
                if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
                    raise ValueError("project archive contains an invalid file row")
                path = row.get("path")
                digest = row.get("sha256")
                size = row.get("size")
                if not isinstance(path, str) or not path or path in seen_paths:
                    raise ValueError("project archive file path is invalid or duplicated")
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError(f"project archive digest is invalid: {path}")
                if type(size) is not int or size < 0:
                    raise ValueError(f"project archive size is invalid: {path}")
                seen_paths.add(path)
                member = f"{ARCHIVE_PROJECT_PREFIX}{path}"
                expected_names.add(member)
                try:
                    info = archive.getinfo(member)
                except KeyError as exc:
                    raise ValueError(f"project archive is missing required file: {path}") from exc
                if info.is_dir() or info.file_size != size:
                    raise ValueError(f"project archive size mismatch: {path}")
                data = archive.read(member)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"project archive digest mismatch: {path}")
                contents[path] = base64.b64encode(data).decode("ascii")
            if set(names) != expected_names:
                extras = sorted(set(names) - expected_names)
                raise ValueError(
                    "project archive contains unlisted members"
                    + (f": {', '.join(extras[:3])}" if extras else "")
                )
            transfer["contents"] = contents
            return manifest, transfer
    except zipfile.BadZipFile as exc:
        raise ValueError("project archive is not a valid ZIP file") from exc


def _write_project_archive(
    repository: FilesystemProjectRepository,
    archive_path: Path,
) -> dict[str, Any]:
    """Write and self-verify one snapshot archive without mutating the project."""

    archive_path = Path(archive_path).resolve()
    project_root = repository.layout.root.resolve()
    if archive_path.is_relative_to(project_root):
        raise ValueError("write the project archive outside the project directory")
    if archive_path.exists():
        raise ValueError(f"archive already exists: {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _archive_manifest(repository)
    transfer = manifest["transfer"]
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.",
        suffix=".tmp",
        dir=archive_path.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            archive.writestr(_zip_info(ARCHIVE_MANIFEST_PATH), _json_bytes(manifest))
            for row in transfer["files"]:
                data = repository.read_transfer_file(row["path"], row["sha256"])
                if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise ValueError(f"project file changed during archive export: {row['path']}")
                archive.writestr(_zip_info(f"{ARCHIVE_PROJECT_PREFIX}{row['path']}"), data)
        checked_manifest, checked_transfer = _read_project_archive(temporary)
        if checked_manifest != manifest or checked_transfer["head"] != transfer["head"]:
            raise ValueError("project archive self-verification disagreed with exported snapshot")
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def _restore_project_archive(
    root: Path,
    archive_path: Path,
) -> tuple[FilesystemProjectRepository, dict[str, Any]]:
    manifest, transfer = _read_project_archive(archive_path)
    project_id = transfer["project_id"]
    if root.name != project_id:
        raise ValueError(
            f"restore directory name must remain the project id {project_id!r}"
        )
    repository = FilesystemProjectRepository.bootstrap_transfer(
        root,
        transfer,
        expected_project_id=project_id,
    )
    repository.verify()
    return repository, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="external project directory; its name is the project id")
    parser.add_argument("--state-record", type=Path, help="authored StateRecord@1 JSON for a new project")
    parser.add_argument("--seats-file", type=Path, help="authored runner seat pack; requires --state-record")
    archive_mode = parser.add_mutually_exclusive_group()
    archive_mode.add_argument("--export-archive", type=Path, help="write the retained project snapshot as a portable ZIP archive")
    archive_mode.add_argument("--restore-archive", type=Path, help="restore a portable project archive into --project")
    args = parser.parse_args(argv)
    try:
        root = args.project.resolve()
        if root.is_relative_to(REPO):
            raise ValueError("choose a project directory outside the source repository")
        require_identifier(root.name, "project_id")
        if args.export_archive or args.restore_archive:
            if args.state_record or args.seats_file:
                raise ValueError("archive export/restore cannot be combined with authored initialization inputs")
            if args.export_archive:
                repository = FilesystemProjectRepository.open(root)
                if repository.load_manifest().project_id != root.name:
                    raise ValueError("project directory name must match retained project identity")
                manifest = _write_project_archive(repository, args.export_archive)
                transfer = manifest["transfer"]
                print(Path(args.export_archive).resolve())
                print(
                    f"Exported project {transfer['project_id']} at version "
                    f"{transfer['head']['version']} with {len(transfer['files'])} retained files."
                )
                print("Excluded runtime state, credentials, caches, rebuildable previews and unbounded logs.")
                return 0
            repository, manifest = _restore_project_archive(root, args.restore_archive)
            transfer = manifest["transfer"]
            print(repository.layout.root)
            print(
                f"Restored project {transfer['project_id']} at version "
                f"{transfer['head']['version']} and verified it through normal project readers."
            )
            return 0

        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError(f"project directory already contains files: {root}")
        if args.seats_file and not args.state_record:
            raise ValueError("--seats-file requires --state-record")
        record = (
            StateRecord.from_dict(json.loads(args.state_record.read_text(encoding="utf-8-sig")))
            if args.state_record
            else StateRecord(project_id=root.name, run_id="authored", entities=())
        )
        if record.project_id != root.name:
            raise ValueError("the state record project_id must match the new directory name")
        if record.base is not None:
            raise ValueError("a retained/bound record cannot initialize a new project; use the complete project copy to continue it")
        seats = None
        if args.seats_file:
            seats = json.loads(args.seats_file.read_text(encoding="utf-8-sig"))
            if not isinstance(seats, dict) or not seats.get("seats"):
                raise ValueError("the seat pack must declare at least one seat")
            if not isinstance(seats.get("commitment_ref"), str) or not seats["commitment_ref"].strip():
                raise ValueError("the seat pack must name its commitment_ref")
            for payload in seats["seats"]:
                _seat(payload)
        repository = FilesystemProjectRepository.initialize(
            root,
            project_id=root.name,
            initial_state={"project_id": root.name, "version": 0},
            authored_record=record.to_dict(),
            seat_pack=seats,
        )
    except (OSError, KeyError, TypeError, ValueError, ProjectRepositoryError) as exc:
        parser.exit(2, f"create_project: {exc}\n")
    print(repository.layout.root)
    print("Created project at version 0; no run or model has been produced.")
    if not record.entities:
        print("The project is empty. Add authored design inputs and seats before running a modeling candidate.")
    elif seats is None:
        print("Supply input/runner/seats.json before running a modeling candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())