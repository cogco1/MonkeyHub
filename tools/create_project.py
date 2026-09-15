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

Report what format a retained project declares, or what migrating it would
require, without writing anything::

    python tools/create_project.py --project D:/work/projects/my-project --inspect-format
    python tools/create_project.py --project D:/work/projects/my-project --plan-migration

Migrate a format-1 project forward into a new empty directory named by the
project id. The source is never written; the migrated copy carries a receipt::

    python tools/create_project.py --project D:/work/projects/my-project \
        --migrate-format --into D:/migrated/my-project

The directory name is the project id, as required by Studio's existing binding.
Archive transport wraps the existing P036 transfer closure; it does not create a
second project format or make ZIP contents authoritative over normal readers.
The format report is the same kind of read-only view: it reads project.json and
the retained closure through normal readers and never rewrites a project.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.repository import (
    CURRENT_FORMAT_VERSION,
    LEGACY_FORMAT_VERSION,
    FilesystemProjectRepository,
    PROJECT_FORMAT_CURRENT,
    PROJECT_FORMAT_SUPPORTED_LEGACY,
    ProjectIntegrityError,
    ProjectMigrationPlan,
    ProjectRepositoryError,
    inspect_project_format,
    plan_project_migration,
    _retained_category,
    embedded_version_identities,
    _write_immutable,
)
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
_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class RetainedVersionReference:
    """One retained location whose value this build reads as a version identity.

    ``shape`` is which of the three retained spellings it is, and ``version``
    is absent for a flat digest field that states no version beside it.
    """

    file: str
    json_path: str
    shape: str
    project_id: str | None
    version: int | None
    state_sha256: str


@dataclass(frozen=True, slots=True)
class UnreadableVersionReference:
    """One retained location in a version-identity shape whose value does not read.

    It is reported rather than dropped: a location this scan saw and could not
    interpret is not a location it covered, and a readable ``project_id`` still
    decides whether the closure belongs to one project.
    """

    file: str
    json_path: str
    shape: str
    project_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class LegacyVersionReferenceScan:
    project_id: str
    references: tuple[RetainedVersionReference, ...]
    unreadable: tuple[UnreadableVersionReference, ...]
    scanned_documents: int
    unopened_files: int


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
    transfer = repository.export_transfer(include_contents=False, include_all_runs=True)
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


def _read_project_archive_source(
    source: str | Path | BinaryIO,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one portable archive and rebuild the existing transfer envelope.

    Nothing is extracted by path. Every member is addressed by the manifest,
    hashed before bootstrap, and then handed to P036's existing transfer
    validator so normal project readers remain the final integrity authority.
    """

    try:
        with zipfile.ZipFile(source, "r") as archive:
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


def _read_project_archive(archive_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return _read_project_archive_source(Path(archive_path).resolve())


def _write_project_archive(
    repository: FilesystemProjectRepository,
    archive_path: Path,
) -> dict[str, Any]:
    """Build, self-verify, then immutably install one noncanonical archive."""

    archive_path = Path(archive_path).resolve()
    project_root = repository.layout.root.resolve()
    if archive_path.is_relative_to(project_root):
        raise ValueError("write the project archive outside the project directory")
    if archive_path.exists():
        raise ValueError(f"archive already exists: {archive_path}")
    manifest = _archive_manifest(repository)
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
                raise ValueError(f"project file changed during archive export: {row['path']}")
            archive.writestr(_zip_info(f"{ARCHIVE_PROJECT_PREFIX}{row['path']}"), data)
    archive_bytes = buffer.getvalue()
    checked_manifest, checked_transfer = _read_project_archive_source(io.BytesIO(archive_bytes))
    if checked_manifest != manifest or checked_transfer["head"] != transfer["head"]:
        raise ValueError("project archive self-verification disagreed with exported snapshot")
    # P036 remains the only generic filesystem writer. The archive is a
    # noncanonical transport artifact, but even its final byte installation is
    # delegated to the existing immutable writer rather than giving this CLI a
    # second filesystem-write authority.
    _write_immutable(archive_path, archive_bytes)
    return manifest


def _restore_project_archive(
    root: Path,
    archive_path: Path,
) -> tuple[FilesystemProjectRepository, dict[str, Any]]:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"restore directory already contains files: {root}")
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


def _collect_legacy_version_references(
    value: Any,
    *,
    file: str,
    path: str = "",
) -> tuple[RetainedVersionReference | UnreadableVersionReference, ...]:
    """Name every version-identity location in one retained payload.

    The walk itself belongs to ``project.repository`` and is shared with the
    migration, so this dry run and the migration receipt cannot disagree about
    what a project embeds. This only says which rows read completely.
    """

    found: list[RetainedVersionReference | UnreadableVersionReference] = []
    for row in embedded_version_identities(value, path):
        if row.detail is not None or row.state_sha256 is None:
            found.append(UnreadableVersionReference(
                file=file, json_path=row.json_pointer, shape=row.shape,
                project_id=row.project_id, detail=row.detail or "no digest",
            ))
            continue
        found.append(RetainedVersionReference(
            file=file, json_path=row.json_pointer, shape=row.shape,
            project_id=row.project_id, version=row.version,
            state_sha256=row.state_sha256,
        ))
    return tuple(found)


def _scan_legacy_version_references(
    plan: ProjectMigrationPlan,
) -> LegacyVersionReferenceScan | None:
    """Scan the already-planned retained closure for exact legacy version refs."""

    if (
        not plan.planned
        or not plan.migration_required
        or plan.inspection.format_version != LEGACY_FORMAT_VERSION
        or plan.target_format_version != CURRENT_FORMAT_VERSION
    ):
        return None
    if plan.project_id is None:
        raise ProjectIntegrityError(
            "MIGRATION_REFERENCE_SCAN_REFUSED: planned project identity is missing"
        )

    repository = FilesystemProjectRepository.open(plan.inspection.root)
    transfer = repository.export_transfer(include_contents=False, include_all_runs=True)
    references: list[RetainedVersionReference] = []
    unreadable: list[UnreadableVersionReference] = []
    scanned = 0
    for entry in transfer["files"]:
        file = entry["path"]
        if _retained_category(file)[0] == "artifact":
            # The plan preserves these byte-for-byte and the planner never opens
            # them; a run workspace holds native exports that are themselves
            # JSON, and reading one would both claim coverage this scan does not
            # have and refuse a project on the contents of an opaque artifact.
            continue
        data = repository.read_transfer_file(file, entry["sha256"])
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectIntegrityError(
                f"MIGRATION_REFERENCE_SCAN_INVALID_JSON: {file}"
            ) from exc
        scanned += 1
        for found in _collect_legacy_version_references(payload, file=file):
            # A readable project_id refuses whatever the other two fields hold:
            # gating this on a fully readable value would let one bad digest
            # carry a foreign identity through as a merely unreadable local one.
            if found.project_id is not None and found.project_id != plan.project_id:
                raise ProjectIntegrityError(
                    "MIGRATION_REFERENCE_SCAN_FOREIGN_PROJECT: "
                    f"{file}{found.json_path} names {found.project_id!r}"
                )
            if isinstance(found, RetainedVersionReference):
                references.append(found)
            else:
                unreadable.append(found)

    def order(item: RetainedVersionReference | UnreadableVersionReference):
        return item.file, item.json_path

    return LegacyVersionReferenceScan(
        project_id=plan.project_id,
        references=tuple(sorted(references, key=order)),
        unreadable=tuple(sorted(unreadable, key=order)),
        scanned_documents=scanned,
        unopened_files=len(transfer["files"]) - scanned,
    )


def _print_migration_plan(
    plan: ProjectMigrationPlan,
    *,
    reference_scan: LegacyVersionReferenceScan | None = None,
    reference_scan_error: str | None = None,
) -> None:
    """Print one dry run: what is retained, what a migration would have to do."""

    inspection = plan.inspection
    print(inspection.root)
    declared = (
        "no readable format" if inspection.format_version is None
        else f"format {inspection.format_version}"
    )
    print(f"{inspection.status} ({declared}): {inspection.detail}.")
    if plan.head is not None:
        print(
            f"Verified project {plan.project_id} at version {plan.head.version} "
            f"through its own format-{inspection.format_version} reader."
        )
    if plan.inventory:
        print(
            f"Retained closure: {plan.retained_files} files, {plan.retained_bytes} bytes, "
            f"{len(plan.run_ids)} run(s)."
        )
        for entry in plan.inventory:
            unknown = " UNREGISTERED" if entry.registered is False else ""
            print(
                f"  {entry.category:<15} {entry.kind:<34} "
                f"{entry.schema or '-':<26} {entry.count:>4} file(s){unknown}"
            )
        if plan.orphan_paths:
            print(f"  orphan records not reachable from HEAD: {len(plan.orphan_paths)}")
    if reference_scan is not None:
        print("Retained project-version identities (the migration lists these in its receipt):")
        print(
            f"  scanned {reference_scan.scanned_documents} retained JSON document(s); "
            f"found {len(reference_scan.references)} identity location(s)."
        )
        for reference in reference_scan.references:
            version = (
                "version unstated" if reference.version is None
                else f"version {reference.version}"
            )
            print(
                f"  {reference.file} {reference.json_path} ({reference.shape}) -> "
                f"{version} {reference.state_sha256}"
            )
        if reference_scan.unreadable:
            print(
                f"  {len(reference_scan.unreadable)} further location(s) are in a "
                f"version-identity shape but hold a value this build cannot read "
                f"as one; none is counted above and none was interpreted:"
            )
            for entry in reference_scan.unreadable:
                print(
                    f"  {entry.file} {entry.json_path} ({entry.shape}) -> "
                    f"MALFORMED, unchecked: {entry.detail}"
                )
        print(
            f"  {reference_scan.unopened_files} retained file(s) were not opened: a "
            f"migration preserves them byte-for-byte, so a project-version identity "
            f"written inside one is not in this list and preserving the file does not "
            f"carry it forward."
        )
        print(
            "  These are the same locations --migrate-format lists in its receipt. "
            "A location whose schema owner declares it is restated by that owner and "
            "everything naming the record moves with it; a location no owner declares "
            "blocks the migration rather than being guessed at."
        )
    elif reference_scan_error is not None:
        print("Retained project-version identities: REFUSED")
        print(f"  - {reference_scan_error}")
    for title, lines in (
        ("Required transformations", plan.required_transformations),
        ("preserved (byte for byte)", plan.preserved),
        ("Blockers", plan.blockers),
    ):
        if lines:
            print(f"{title}:")
            for line in lines:
                print(f"  - {line}")
    if plan.migration_required:
        print(
            "Back up before any migration is attempted (the archive requires "
            "this directory to be named by its project id): "
            f"python tools/create_project.py --project {inspection.root} "
            "--export-archive <archive.zip>"
        )
        print(
            "Then migrate into a new empty directory named for the project; the "
            "migration never writes this one, so it is a backup by construction: "
            f"python tools/create_project.py --project {inspection.root} "
            "--migrate-format --into <target>"
        )
    if not plan.planned:
        print("The retained closure was not inventoried; see the blockers above.")
    print("No file in the project was created or changed.")


def main(argv: list[str] | None = None) -> int:
    # A project path can hold any script; a cp1252 console would otherwise turn
    # a finished migration into a UnicodeEncodeError on the line reporting it.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="external project directory; its name is the project id")
    parser.add_argument("--state-record", type=Path, help="authored StateRecord@1 JSON for a new project")
    parser.add_argument("--seats-file", type=Path, help="authored runner seat pack; requires --state-record")
    archive_mode = parser.add_mutually_exclusive_group()
    archive_mode.add_argument("--export-archive", type=Path, help="write the retained project snapshot as a portable ZIP archive")
    archive_mode.add_argument("--restore-archive", type=Path, help="restore a portable project archive into --project")
    archive_mode.add_argument("--inspect-format", action="store_true", help="report the project format this directory declares; writes nothing")
    archive_mode.add_argument("--plan-migration", action="store_true", help="dry-run report of the retained closure and what a format migration would require; writes nothing")
    archive_mode.add_argument("--migrate-format", action="store_true", help="migrate a format-1 project into a new format-2 directory named by --into; the source is never written")
    parser.add_argument("--into", type=Path, help="empty target directory for --migrate-format; its name must be the project id")
    args = parser.parse_args(argv)
    if args.into is not None and not args.migrate_format:
        parser.error("--into is only meaningful with --migrate-format")
    if args.migrate_format and args.into is None:
        parser.error("--migrate-format needs --into <empty target directory>")
    try:
        root = args.project.resolve()
        if root.is_relative_to(REPO):
            raise ValueError("choose a project directory outside the source repository")
        if not args.migrate_format:
            # A migration reads the project id from project.json and names the
            # target by it; the source directory's own name is not its identity.
            require_identifier(root.name, "project_id")
        if args.inspect_format or args.plan_migration:
            if args.state_record or args.seats_file:
                raise ValueError("a format report cannot be combined with authored initialization inputs")
            if args.inspect_format:
                inspection = inspect_project_format(root)
                print(inspection.root)
                print(f"{inspection.status}: {inspection.detail}.")
                # A readable format is a successful report even when the
                # project itself still needs work; anything else failed closed.
                return 0 if inspection.status in (
                    PROJECT_FORMAT_CURRENT, PROJECT_FORMAT_SUPPORTED_LEGACY,
                ) else 2
            plan = plan_project_migration(root)
            reference_scan = None
            reference_scan_error = None
            try:
                reference_scan = _scan_legacy_version_references(plan)
            except ProjectRepositoryError as exc:
                reference_scan_error = str(exc)
            _print_migration_plan(
                plan,
                reference_scan=reference_scan,
                reference_scan_error=reference_scan_error,
            )
            # The dry run is a complete diagnostic only when its exact
            # legacy-reference scan also completes; --migrate-format acts on it.
            return 0 if plan.planned and reference_scan_error is None else 2
        if args.migrate_format:
            if args.state_record or args.seats_file:
                raise ValueError("a migration cannot be combined with authored initialization inputs")
            target = args.into.resolve()
            if target.is_relative_to(REPO):
                raise ValueError("choose a target directory outside the source repository")
            result = FilesystemProjectRepository.migrate_project_format(root, target)
            print(result.target_root)
            print(f"migrated format {result.source_format_version} -> {result.target_format_version}: "
                  f"{len(result.versions)} version(s), {len(result.rewritten)} rewritten file(s), "
                  f"{result.preserved_files} preserved file(s), "
                  f"{len(result.embedded_legacy_references)} retained record reference(s) still legacy-shaped")
            if result.orphans:
                print(f"carried {len(result.orphans)} unreachable canonical/event record(s) forward unchanged")
            for name, paths in (
                ("binary file(s) containing a legacy digest, not opened", result.unscanned_binaries),
                ("retained JSON file(s) this build could not decode", result.undecodable),
            ):
                if paths:
                    print(f"{len(paths)} {name}: " + ", ".join(paths))
            print(f"receipt: {result.receipt.uri if result.receipt else 'none'}")
            return 0
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
