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
import contextlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.archive import (
    restore_project_archive,
    write_project_archive,
)
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
)
from archflow.project.refs import require_identifier
from archflow.project.version_ref_owners import load_workflow_owners
from archflow.state.state_record import StateRecord
from tools.run_project import _seat


_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})


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
    # The shared core cannot import a workflow package, so this command - which
    # is above both layers - loads the workflow owners itself. Without it a
    # project holding a drawing sheet is refused for a contract this build
    # does state.
    load_workflow_owners()
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
            if result.unscanned_binaries:
                print(f"{len(result.unscanned_binaries)} binary file(s) containing a "
                      f"legacy digest, not opened: " + ", ".join(result.unscanned_binaries))
            if result.orphan_legacy_references:
                print(f"{len(result.orphan_legacy_references)} location(s) inside records "
                      f"the published chain does not reach still name a legacy version; "
                      f"they were carried over unchanged and are listed in the receipt")
            print(f"receipt: {result.receipt.uri if result.receipt else 'none'}")
            return 0
        if args.export_archive or args.restore_archive:
            if args.state_record or args.seats_file:
                raise ValueError("archive export/restore cannot be combined with authored initialization inputs")
            if args.export_archive:
                repository = FilesystemProjectRepository.open(root)
                if repository.load_manifest().project_id != root.name:
                    raise ValueError("project directory name must match retained project identity")
                summary = write_project_archive(repository, args.export_archive.resolve())
                print(summary.archive_path)
                print(
                    f"Exported project {summary.project_id} at version "
                    f"{summary.version} with {summary.file_count} retained files."
                )
                print("Excluded runtime state, credentials, caches, rebuildable previews and unbounded logs.")
                return 0
            repository, summary = restore_project_archive(root, args.restore_archive.resolve())
            print(repository.layout.root)
            print(
                f"Restored project {summary.project_id} at version "
                f"{summary.version} and verified it through normal project readers."
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
