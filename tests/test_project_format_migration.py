"""Read-only project-format inspection and dry-run migration planning.

Every fixture here is a disposable project built inside the test's own
temporary directory. Nothing reads a user project, and every test that plans a
migration compares the whole project directory, advisory lock files included,
before and after: planning a project must never create or change a file in it.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, PROJECT_FORMAT_MIGRATION, STATE_RECORD
from archflow.project.refs import ProjectVersionRef, record_file_name
from archflow.project.repository import (
    CURRENT_FORMAT_VERSION,
    FilesystemProjectRepository,
    LEGACY_FORMAT_VERSION,
    PROJECT_FORMAT_CURRENT,
    PROJECT_FORMAT_INVALID,
    PROJECT_FORMAT_SUPPORTED_LEGACY,
    PROJECT_FORMAT_TOO_NEW,
    MIGRATION_RUN_ID,
    ProjectIntegrityError,
    _json_bytes,
    _replace_atomic,
    _sha256,
    _write_immutable,
    inspect_project_format,
    plan_project_migration,
)
from tools.create_project import _scan_legacy_version_references, main


class ProjectFormatPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    # ---- fixtures

    def current_project(self, name: str = "current-building") -> FilesystemProjectRepository:
        """A project this build creates itself, at the current format version."""

        repository = FilesystemProjectRepository.initialize(
            self.root / name,
            project_id=name,
            initial_state={"phase": "design"},
            authored_record={"schema": "StateRecord@1", "draft": "initial"},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        return repository

    def legacy_envelope(self, name: str) -> Path:
        """A format-1 project exactly as an older build first wrote one.

        The bytes are the historical ones: ``CanonicalSnapshot@1`` carries no
        semantic digest, and the version reference names the snapshot file.
        Nothing here has ever been opened for writing, so the project carries
        none of the repository's advisory lock files.
        """

        root = self.root / name
        for directory in ("canonical", "events", "runs"):
            (root / directory).mkdir(parents=True)
        _write_immutable(
            root / "project.json",
            _json_bytes({
                "schema": "ArchFlowProject@1",
                "project_id": name,
                "format_version": LEGACY_FORMAT_VERSION,
            }),
        )
        self.write_legacy_version(root, {"phase": "design"})
        return root

    def write_legacy_version(
        self, root: Path, state: dict, **event_fields: object,
    ) -> ProjectVersionRef:
        """Publish one more version exactly as an older build wrote it.

        ``compare_and_swap`` needs a promotion receipt and a run of its own, so
        a multi-version legacy fixture is written as the historical bytes. The
        same helper writes v0, so the fixture has one spelling of a published
        legacy version rather than two that can drift apart.
        """

        head_path = root / "HEAD"
        previous = (
            json.loads(head_path.read_text(encoding="utf-8"))
            if head_path.exists() else None
        )
        project_id = root.name
        parent = previous["current"] if previous else None
        version = 0 if parent is None else parent["version"] + 1
        snapshot_bytes = _json_bytes({
            "schema": "CanonicalSnapshot@1",
            "project_id": project_id,
            "version": version,
            "parent": parent,
            "state": dict(state),
        })
        snapshot_digest = _sha256(snapshot_bytes)
        snapshot_path = f"canonical/{record_file_name(f'state-v{version:06d}', snapshot_digest)}"
        _write_immutable(root / snapshot_path, snapshot_bytes)
        head_ref = ProjectVersionRef(project_id, version, snapshot_digest)
        event_bytes = _json_bytes({
            "schema": "ProjectEvent@1",
            "project_id": project_id,
            "event_type": "project.initialized" if parent is None else "candidate.promoted",
            "decision": "accepted",
            "run_id": None,
            "from": parent,
            "to": head_ref.to_dict(),
            "previous_event": previous["event"] if previous else None,
            "decision_receipt": None,
            **event_fields,
        })
        event_digest = _sha256(event_bytes)
        event_path = f"events/{record_file_name(f'event-v{version:06d}', event_digest)}"
        _write_immutable(root / event_path, event_bytes)
        head_payload = _json_bytes({
            "schema": "ProjectHead@1",
            "project_id": project_id,
            "current": head_ref.to_dict(),
            "snapshot": {"relative_path": snapshot_path, "sha256": snapshot_digest,
                         "media_type": "application/json"},
            "event": {"relative_path": event_path, "sha256": event_digest,
                      "media_type": "application/json"},
        })
        if previous is None:
            _write_immutable(head_path, head_payload)
        else:
            _replace_atomic(head_path, head_payload)
        return head_ref

    def legacy_project(self, name: str = "legacy-building") -> FilesystemProjectRepository:
        """A format-1 project an older build went on to author and use."""

        repository = FilesystemProjectRepository.open(self.legacy_envelope(name))
        repository.initialize_authored_inputs(
            expected_head=repository.read_head(),
            expected_record=None,
            authored_record={"schema": "StateRecord@1", "draft": "initial"},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        return repository

    def populate(self, repository: FilesystemProjectRepository) -> None:
        """Give a project retained runs, records, a design branch and bytes.

        The second run is never referenced from HEAD or from the design branch,
        so a closure that only followed published history would miss it.
        """

        stage = self.stage(repository, "first")
        repository.compare_and_swap_design_branch(
            branch_id="main",
            expected_head=None,
            branch={"branch_id": "main", "parent_branch": None,
                    "fork_stage": stage.to_dict(), "head_stage": stage.to_dict()},
        )
        self.stage(repository, "extra")

    def stage(self, repository: FilesystemProjectRepository, name: str):
        run = repository.create_run(name)
        artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=name,
            media_type="model/3dm",
            source=io.BytesIO(f"model-{name}".encode()),
        )
        record = repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=name),
            record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "run": run.to_dict(), "model": {
                "project_id": run.project_id, "relative_path": artifact.relative_path,
                "sha256": artifact.sha256, "media_type": artifact.media_type}},
        )
        return repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
            record_kind=DESIGN_STAGE,
            payload={"schema": "DesignStage@1", "candidate_id": name,
                     "parent_stage": None, "record": record.to_dict()},
        )

    def install_historical_record(
        self, repository: FilesystemProjectRepository, kind: str,
        *, embeds_base: bool = True, note: str = "historical",
    ) -> Path:
        """Write a retained record whose kind this build no longer registers.

        Archived lanes left records like this behind; ``put_json`` refuses to
        write one now, but readers must keep them. Whether such a record
        *embeds a project-version identity* is the whole question a migration
        has to answer, so the fixture can write either kind.
        """

        payload = {
            "schema": "RetiredLaneNote@1",
            "project_id": repository.layout.project_id,
            "note": note,
        }
        if embeds_base:
            payload["base"] = repository.read_head().to_dict()
        data = _json_bytes(payload)
        path = (repository.layout.run("extra").records
                / record_file_name(kind, _sha256(data)))
        _write_immutable(path, data)
        return path

    def corrupt_a_retained_record(self, repository: FilesystemProjectRepository) -> None:
        """Make one retained record disagree with the digest in its file name."""

        for path in sorted((repository.layout.run("extra").records).glob("*.json")):
            path.chmod(0o644)
            path.write_bytes(b'{"schema": "StateRecord@1"}\n')

    # ---- the whole project directory, locks included

    def fingerprint(self, root: Path) -> dict[str, tuple[int, str]]:
        return {
            path.relative_to(root).as_posix(): (
                path.stat().st_size, _sha256(path.read_bytes()),
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def assert_unchanged(self, root: Path, before: dict[str, tuple[int, str]]) -> None:
        self.assertEqual(self.fingerprint(root), before)

    # ---- detection

    def test_current_project_is_detected_as_current_and_needs_no_migration(self) -> None:
        repository = self.current_project()
        root = repository.layout.root
        before = self.fingerprint(root)

        inspection = inspect_project_format(root)
        self.assertEqual(inspection.status, PROJECT_FORMAT_CURRENT)
        self.assertEqual(inspection.format_version, CURRENT_FORMAT_VERSION)
        self.assertEqual(inspection.project_id, "current-building")

        plan = plan_project_migration(root)
        self.assertTrue(plan.planned)
        self.assertFalse(plan.migration_required)
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.required_transformations, ())
        self.assertEqual(plan.head, repository.read_head())
        self.assert_unchanged(root, before)

    def test_legacy_project_is_supported_legacy_and_never_reported_upgradeable(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        before = self.fingerprint(root)

        inspection = inspect_project_format(root)
        self.assertEqual(inspection.status, PROJECT_FORMAT_SUPPORTED_LEGACY)
        self.assertEqual(inspection.format_version, LEGACY_FORMAT_VERSION)

        plan = plan_project_migration(root)
        self.assertTrue(plan.planned)
        self.assertTrue(plan.migration_required)
        self.assertEqual(plan.blockers, ())
        reported = " ".join(
            (plan.inspection.detail, *plan.required_transformations,
             *plan.preserved, *plan.blockers)
        )
        self.assertNotIn("upgradeable", reported)
        self.assert_unchanged(root, before)

    def test_newer_format_fails_closed_without_being_opened(self) -> None:
        repository = self.current_project()
        root = repository.layout.root
        manifest = root / "project.json"
        manifest.chmod(0o644)
        manifest.write_bytes(_json_bytes({
            "schema": "ArchFlowProject@1",
            "project_id": "current-building",
            "format_version": CURRENT_FORMAT_VERSION + 1,
        }))
        before = self.fingerprint(root)

        inspection = inspect_project_format(root)
        self.assertEqual(inspection.status, PROJECT_FORMAT_TOO_NEW)
        self.assertEqual(inspection.format_version, CURRENT_FORMAT_VERSION + 1)
        self.assertIn("newer build", inspection.detail)
        self.assertIn("never downgraded", inspection.detail)

        plan = plan_project_migration(root)
        self.assertFalse(plan.planned)
        self.assertFalse(plan.migration_required)
        self.assertEqual(plan.blockers, (inspection.detail,))
        self.assertEqual(plan.inventory, ())
        with self.assertRaises(ProjectIntegrityError):
            FilesystemProjectRepository.open(root)
        self.assert_unchanged(root, before)

    def test_malformed_or_missing_manifest_is_invalid(self) -> None:
        missing = self.root / "not-a-project"
        missing.mkdir()
        self.assertEqual(inspect_project_format(missing).status, PROJECT_FORMAT_INVALID)

        repository = self.current_project()
        root = repository.layout.root
        manifest = root / "project.json"
        manifest.chmod(0o644)
        manifest.write_bytes(b'{"schema": "ArchFlowProject@1", "project_id"')
        before = self.fingerprint(root)
        inspection = inspect_project_format(root)
        self.assertEqual(inspection.status, PROJECT_FORMAT_INVALID)
        self.assertIsNone(inspection.format_version)

        plan = plan_project_migration(root)
        self.assertFalse(plan.planned)
        self.assertEqual(plan.blockers, (inspection.detail,))
        self.assert_unchanged(root, before)

    def test_drifted_manifest_field_set_is_invalid_not_a_version(self) -> None:
        repository = self.current_project()
        manifest = repository.layout.root / "project.json"
        manifest.chmod(0o644)
        manifest.write_bytes(_json_bytes({
            "schema": "ArchFlowProject@1",
            "project_id": "current-building",
            "format_version": 2,
            "component_schema_versions": {"StateRecord": 1},
        }))
        inspection = inspect_project_format(manifest.parent)
        self.assertEqual(inspection.status, PROJECT_FORMAT_INVALID)
        self.assertIsNone(inspection.format_version)

    # ---- the source project is never written to

    def test_planning_a_readable_project_creates_and_changes_no_file(self) -> None:
        for repository in (self.current_project(), self.legacy_project()):
            with self.subTest(project=repository.layout.project_id):
                root = repository.layout.root
                before = self.fingerprint(root)
                self.assertTrue(plan_project_migration(root).planned)
                self.assert_unchanged(root, before)

    def test_planning_refuses_a_project_it_cannot_read_without_writing_to_it(self) -> None:
        """An untouched project has no advisory locks, and taking one creates it.

        The closure is read through the repository's own guarded export, so a
        project that has never been opened for writing cannot be inventoried
        without changing it. The refusal names the exact lock paths instead.
        """

        root = self.legacy_envelope("untouched-building")
        before = self.fingerprint(root)
        self.assertEqual(
            [name for name in before if name.endswith(".lock")], [],
        )

        self.assertEqual(
            inspect_project_format(root).status, PROJECT_FORMAT_SUPPORTED_LEGACY,
        )
        plan = plan_project_migration(root)
        self.assertFalse(plan.planned)
        self.assertEqual(plan.inventory, ())
        self.assertEqual(len(plan.blockers), 1)
        self.assertIn("HEAD.lock", plan.blockers[0])
        self.assertIn("design/branches.lock", plan.blockers[0])
        self.assertIn("would change it", plan.blockers[0])
        self.assert_unchanged(root, before)
        self.assertFalse((root / "design").exists())

    # ---- inventory

    def test_plan_inventories_the_complete_retained_closure(self) -> None:
        repository = self.current_project()
        root = repository.layout.root
        before = self.fingerprint(root)

        plan = plan_project_migration(root)
        self.assertEqual(plan.run_ids, ("extra", "first"))
        self.assertEqual(
            {entry.category for entry in plan.inventory},
            {"envelope", "record", "run_manifest", "design", "authored_input", "artifact"},
        )
        schemas = {entry.schema for entry in plan.inventory if entry.schema}
        for declared in ("ArchFlowProject@1", "ProjectHead@2", "CanonicalSnapshot@2",
                         "ProjectEvent@2", "ProjectRun@1", "DesignBranches@1",
                         "StateRecord@1", "DesignStage@1", "SeatPack@1"):
            self.assertIn(declared, schemas)
        self.assertEqual(
            {entry.kind for entry in plan.inventory if entry.category == "authored_input"},
            {"input/runner/state-record.json", "input/runner/seats.json"},
        )
        artifacts = [entry for entry in plan.inventory if entry.category == "artifact"]
        self.assertEqual([entry.kind for entry in artifacts], ["objects/sha256"])
        self.assertEqual(sum(entry.count for entry in artifacts), 2)
        self.assertIsNone(artifacts[0].schema)
        self.assertEqual(plan.retained_files, sum(entry.count for entry in plan.inventory))
        self.assertEqual(plan.retained_bytes, sum(entry.bytes for entry in plan.inventory))
        self.assert_unchanged(root, before)

    def test_inventory_covers_a_run_no_published_history_references(self) -> None:
        plan = plan_project_migration(self.current_project().layout.root)
        self.assertEqual(
            [entry.count for entry in plan.inventory if entry.category == "run_manifest"],
            [2],
        )
        self.assertIn("extra", plan.run_ids)
        records = {
            entry.kind: entry.count
            for entry in plan.inventory
            if entry.category == "record"
        }
        self.assertEqual(records[STATE_RECORD], 2)
        self.assertEqual(records[DESIGN_STAGE], 2)

    def test_unknown_retained_record_is_preserved_and_listed(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        self.install_historical_record(repository, "retired-lane-note", embeds_base=False)
        before = self.fingerprint(root)

        plan = plan_project_migration(root)
        unknown = [entry for entry in plan.inventory if entry.registered is False]
        self.assertEqual([entry.kind for entry in unknown], ["retired-lane-note"])
        self.assertEqual(unknown[0].schema, "RetiredLaneNote@1")
        preserved = [line for line in plan.preserved if "retired-lane-note" in line]
        self.assertEqual(len(preserved), 1)
        self.assertIn("RetiredLaneNote@1", preserved[0])
        self.assertIn("preserves it byte for byte", preserved[0])
        self.assertEqual(plan.blockers, ())
        self.assert_unchanged(root, before)

    def test_a_complete_legacy_closure_plans_with_no_blockers(self) -> None:
        repository = self.legacy_project()
        plan = plan_project_migration(repository.layout.root)
        self.assertTrue(plan.planned)
        self.assertTrue(plan.migration_required)
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.preserved, ())
        self.assertTrue(any("state_sha256 becomes the semantic state digest" in t for t in plan.required_transformations))

    def test_legacy_plan_names_the_envelope_work_a_migration_would_require(self) -> None:
        plan = plan_project_migration(self.legacy_project().layout.root)
        required = "\n".join(plan.required_transformations)
        self.assertIn("project.json format_version 1 -> 2", required)
        self.assertIn("HEAD ProjectHead@1 -> ProjectHead@2", required)
        self.assertIn("CanonicalSnapshot@1 -> @2", required)
        self.assertIn("ProjectEvent@1 -> @2", required)
        self.assertIn("byte-for-byte", required)

    def test_unknown_kind_with_declared_and_missing_schema_remains_readable(self) -> None:
        repository = self.legacy_project()
        self.install_historical_record(repository, "retired-lane-note", embeds_base=False)
        data = _json_bytes({"note": "historical record without a schema field"})
        _write_immutable(
            repository.layout.run("extra").records
            / record_file_name("retired-lane-note", _sha256(data)), data,
        )
        before = self.fingerprint(repository.layout.root)
        plan = plan_project_migration(repository.layout.root)
        self.assertTrue(plan.planned)
        entries = [entry for entry in plan.inventory if entry.kind == "retired-lane-note"]
        self.assertEqual({entry.schema for entry in entries}, {None, "RetiredLaneNote@1"})
        self.assertEqual(len([line for line in plan.preserved if "retired-lane-note" in line]), 2)
        self.assertEqual(plan.blockers, ())
        self.assert_unchanged(repository.layout.root, before)

    def test_the_plan_never_claims_to_have_analyzed_record_references(self) -> None:
        """Record payloads are not parsed, so no reference count is reported.

        A retained record embeds artifact references that share a key set with
        a record reference. Counting those by shape would be a guess, so the
        plan defers every payload question to that record's typed owner.
        """

        plan = plan_project_migration(self.legacy_project().layout.root)
        reported = (*plan.required_transformations, *plan.preserved, *plan.blockers)
        for line in reported:
            self.assertNotRegex(line, r"\d+ (typed )?(record|project-version) reference")
        deferred = [
            line for line in plan.required_transformations
            if "pointers its schema's owner declares" in line
        ]
        self.assertEqual(len(deferred), 1)
        self.assertIn("4 retained record(s)", deferred[0])
        self.assertIn("blocks the migration", deferred[0])

    # ---- refusals

    def test_migration_is_forward_only(self) -> None:
        plan = plan_project_migration(
            self.current_project().layout.root,
            target_format_version=LEGACY_FORMAT_VERSION,
        )
        self.assertFalse(plan.planned)
        self.assertEqual(len(plan.blockers), 1)
        self.assertIn("forward-only", plan.blockers[0])

    def test_an_unsupported_target_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            plan_project_migration(
                self.current_project().layout.root,
                target_format_version=CURRENT_FORMAT_VERSION + 1,
            )

    def test_a_corrupt_retained_closure_cannot_be_planned(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        self.corrupt_a_retained_record(repository)
        before = self.fingerprint(root)

        plan = plan_project_migration(root)
        self.assertFalse(plan.planned)
        self.assertEqual(plan.inventory, ())
        self.assertEqual(plan.retained_files, 0)
        self.assertEqual(len(plan.blockers), 1)
        self.assertIn("could not be read completely", plan.blockers[0])
        self.assert_unchanged(root, before)

    def test_a_record_that_changes_during_the_scan_cannot_be_planned(self) -> None:
        """A partial reading is never presented as the complete closure."""

        repository = self.legacy_project()
        root = repository.layout.root
        real = FilesystemProjectRepository.read_transfer_file
        calls: list[str] = []

        def fail_after_three(self, path: str, sha256: str) -> bytes:
            calls.append(path)
            if len(calls) > 3:
                raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: shared file changed")
            return real(self, path, sha256)

        with patch.object(
            FilesystemProjectRepository, "read_transfer_file", fail_after_three,
        ):
            plan = plan_project_migration(root)
        self.assertFalse(plan.planned)
        self.assertEqual(plan.inventory, ())
        self.assertIn("could not be read completely", plan.blockers[0])
        self.assertIn("shared file changed", plan.blockers[0])

    # ---- the migration

    def test_migration_writes_a_format_2_project_beside_an_untouched_source(self) -> None:
        repository = self.legacy_project()
        source = repository.layout.root
        before = self.fingerprint(source)
        target = self.root / "migrated" / "legacy-building"

        result = FilesystemProjectRepository.migrate_project_format(source, target)

        self.assert_unchanged(source, before)
        self.assertEqual((result.source_format_version, result.target_format_version), (1, CURRENT_FORMAT_VERSION))
        self.assertEqual(inspect_project_format(target).status, PROJECT_FORMAT_CURRENT)
        migrated = FilesystemProjectRepository.open(target)
        migrated.verify()
        closure = migrated.export_transfer(include_contents=False, include_all_runs=True)
        self.assertEqual(sorted(closure["run_ids"]), ["extra", "first", MIGRATION_RUN_ID])
        # Every run base names a published format-2 version.
        for run_id in closure["run_ids"]:
            migrated.load_version_state(migrated.load_run(run_id).base)
        # The version table covers the whole history and HEAD is its last row.
        versions = {version: (legacy, semantic) for version, legacy, semantic in result.versions}
        self.assertEqual(sorted(versions), list(range(repository.read_head().version + 1)))
        self.assertEqual(migrated.read_head().state_sha256, versions[repository.read_head().version][1])
        self.assertEqual(versions[repository.read_head().version][0], repository.read_head().state_sha256)
        # The state content itself is what the legacy reader says it is.
        for version, (legacy, semantic) in versions.items():
            self.assertEqual(migrated.load_version_state(ProjectVersionRef(repository.layout.project_id, version, semantic)),
                             repository.load_version_state(ProjectVersionRef(repository.layout.project_id, version, legacy)))
        self.assertIn("runs/first/run.json", result.rewritten)
        self.assertFalse(plan_project_migration(target).migration_required)

    def test_migration_maps_every_published_version_of_a_longer_history(self) -> None:
        root = self.legacy_envelope("history-building")
        second = self.write_legacy_version(root, {"phase": "developed"})
        repository = FilesystemProjectRepository.open(root)
        self.populate(repository)
        before = self.fingerprint(root)
        target = self.root / "migrated" / "history-building"

        result = FilesystemProjectRepository.migrate_project_format(root, target)

        self.assert_unchanged(root, before)
        self.assertEqual([version for version, _, _ in result.versions], [0, 1])
        self.assertEqual(result.versions[1][1], second.state_sha256)
        migrated = FilesystemProjectRepository.open(target)
        self.assertEqual(migrated.read_head().version, 1)
        self.assertEqual(migrated.read_head().state_sha256, result.versions[1][2])
        for version, legacy, semantic in result.versions:
            self.assertEqual(
                migrated.load_version_state(ProjectVersionRef("history-building", version, semantic)),
                repository.load_version_state(ProjectVersionRef("history-building", version, legacy)),
            )
        # The runs were based on v1, and they name the migrated v1.
        self.assertEqual(migrated.load_run("first").base,
                         ProjectVersionRef("history-building", 1, result.versions[1][2]))

    def test_an_unregistered_record_holding_a_legacy_identity_blocks_the_migration(self) -> None:
        """No owner can say what restating it means, so nothing is written."""

        repository = self.legacy_project()
        note = self.install_historical_record(repository, "retired-lane-note")
        source = repository.layout.root
        target = self.root / "migrated" / "legacy-building"

        # The planner names it too, grouped by kind with one example location.
        plan = plan_project_migration(source)
        self.assertEqual(len(plan.blockers), 1, plan.blockers)
        self.assertIn("retired-lane-note", plan.blockers[0])
        self.assertIn("1 record(s)", plan.blockers[0])
        self.assertIn("/base", plan.blockers[0])
        self.assertEqual(plan.preserved, ())

        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(source, target)

        message = str(raised.exception)
        self.assertIn("MIGRATION_BLOCKED", message)
        self.assertIn(note.relative_to(source).as_posix(), message)
        self.assertIn("RetiredLaneNote@1", message)
        self.assertIn("/base", message)
        self.assertIn("no owner in this build restates", message)
        self.assertFalse(target.exists())

    def test_a_record_whose_kind_is_registered_but_whose_location_is_not_blocks(self) -> None:
        """Registration is not a declaration: the pointer has to be declared."""

        repository = self.legacy_project()
        source = repository.layout.root
        head = repository.read_head()
        # A real StateRecord@1 declares /base and nothing else, so a second
        # identity somewhere else in the same record has no owner.
        data = _json_bytes({
            "schema": "StateRecord@1",
            "base": head.to_dict(),
            "superseded": head.to_dict(),
        })
        _write_immutable(
            repository.layout.run("extra").records
            / record_file_name(STATE_RECORD, _sha256(data)), data,
        )
        target = self.root / "migrated" / "legacy-building"

        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(source, target)

        message = str(raised.exception)
        self.assertIn("MIGRATION_BLOCKED", message)
        self.assertIn("/superseded", message)
        self.assertNotIn("/base that no owner", message)
        self.assertFalse(target.exists())

    def test_restating_a_record_moves_every_reference_that_names_it(self) -> None:
        """Two levels: the branch names a stage, the stage names a state record."""

        repository = self.legacy_project()
        source = repository.layout.root
        before = {
            path.relative_to(source).as_posix()
            for path in source.rglob("*") if path.is_file()
        }
        target = self.root / "migrated" / "legacy-building"

        result = FilesystemProjectRepository.migrate_project_format(source, target)

        migrated = FilesystemProjectRepository.open(target)
        # The state record moved because its run base was restated; the design
        # stage that names it moved with it; the branch that names the stage
        # was rewritten to follow. verify() resolves the whole chain.
        migrated.verify()
        moved = {row for row in result.rewritten if " -> " in row}
        self.assertTrue(
            any("state-record-" in row for row in moved), sorted(result.rewritten),
        )
        self.assertTrue(
            any("design-stage-" in row for row in moved), sorted(result.rewritten),
        )
        self.assertIn("design/branches.json", result.rewritten)
        after = {
            path.relative_to(target).as_posix()
            for path in target.rglob("*") if path.is_file()
        }
        # Every retained record that moved left no copy at its old name.
        for row in moved:
            old, new = row.split(" -> ")
            self.assertIn(old, before)
            self.assertNotIn(old, after)
            self.assertIn(new, after)
        self.assertEqual(result.embedded_legacy_references, ())
        # Nothing still names a version by its legacy digest, except the
        # migration receipt, whose whole job is to state the identity map.
        legacy = {legacy for _, legacy, _ in result.versions}
        receipt = result.receipt.relative_path
        for path in sorted(target.rglob("*.json")):
            if path.match("runs/*/workspaces/*"):
                continue
            if path.relative_to(target).as_posix() == receipt:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for digest in legacy:
                self.assertNotIn(digest, text, path.relative_to(target).as_posix())

    def test_migration_rewrites_the_authored_state_record_base(self) -> None:
        root = self.legacy_envelope("authored-building")
        repository = FilesystemProjectRepository.open(root)
        head = repository.read_head()
        repository.initialize_authored_inputs(
            expected_head=head, expected_record=None,
            authored_record={"schema": "StateRecord@1", "draft": "initial", "base": head.to_dict()},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        target = self.root / "migrated" / "authored-building"
        result = FilesystemProjectRepository.migrate_project_format(root, target)
        authored = json.loads((target / "input" / "runner" / "state-record.json").read_text(encoding="utf-8"))
        self.assertEqual(authored["base"]["state_sha256"], FilesystemProjectRepository.open(target).read_head().state_sha256)
        self.assertEqual(authored["draft"], "initial")
        self.assertIn("input/runner/state-record.json", result.rewritten)
        # Seats were not the migration's to touch.
        self.assertEqual((target / "input" / "runner" / "seats.json").read_bytes(), (root / "input" / "runner" / "seats.json").read_bytes())

    def test_migrated_project_continues_with_a_new_run_from_head(self) -> None:
        repository = self.legacy_project()
        target = self.root / "migrated" / "legacy-building"
        FilesystemProjectRepository.migrate_project_format(repository.layout.root, target)
        migrated = FilesystemProjectRepository.open(target)
        head = migrated.read_head()
        run = migrated.create_run("after-migration")
        self.assertEqual(run.base, head)
        self.stage(migrated, "after-migration")
        migrated.verify()
        self.assertEqual(migrated.read_head(), head)

    def test_migration_refuses_a_current_project_a_wrong_target_name_and_a_used_target(self) -> None:
        current = self.current_project()
        with self.assertRaises(ProjectIntegrityError):
            FilesystemProjectRepository.migrate_project_format(current.layout.root, self.root / "x" / "current-building")
        legacy = self.legacy_project()
        with self.assertRaises(ProjectIntegrityError):
            FilesystemProjectRepository.migrate_project_format(legacy.layout.root, self.root / "x" / "other-name")
        used = self.root / "x" / "legacy-building"
        used.mkdir(parents=True)
        (used / "note.txt").write_text("busy", encoding="utf-8")
        with self.assertRaises(ProjectIntegrityError):
            FilesystemProjectRepository.migrate_project_format(legacy.layout.root, used)

    def test_migration_refuses_a_source_that_redirects_through_a_junction(self) -> None:
        """A junction is not a symlink here, and copytree would inline it."""

        repository = self.legacy_project()
        source = repository.layout.root
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("not this project", encoding="utf-8")
        link = source / "runs" / "linked"
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                check=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            self.skipTest(f"this host cannot create a junction: {exc}")
        self.addCleanup(lambda: link.exists() and link.rmdir())
        target = self.root / "migrated" / "legacy-building"
        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(source, target)
        self.assertIn("MIGRATION_SOURCE_REDIRECTED", str(raised.exception))
        self.assertFalse(target.exists())

    def test_migration_refuses_a_source_that_published_while_it_was_copied(self) -> None:
        repository = self.legacy_project()
        source = repository.layout.root
        target = self.root / "migrated" / "legacy-building"
        real_copytree = shutil.copytree

        def publish_midway(*args, **kwargs):
            result = real_copytree(*args, **kwargs)
            self.write_legacy_version(source, {"phase": "moved"})
            return result

        with patch.object(shutil, "copytree", publish_midway):
            with self.assertRaises(ProjectIntegrityError) as raised:
                FilesystemProjectRepository.migrate_project_format(source, target)
        self.assertIn("MIGRATION_SOURCE_MOVED", str(raised.exception))
        self.assertFalse(target.exists())

    def test_migration_carries_unreachable_canonical_records_forward(self) -> None:
        """A promotion interrupted before its HEAD swap leaves these behind."""

        root = self.legacy_envelope("interrupted-building")
        head = json.loads((root / "HEAD").read_text(encoding="utf-8"))
        stranded = _json_bytes({
            "schema": "CanonicalSnapshot@1", "project_id": "interrupted-building",
            "version": 1, "parent": head["current"], "state": {"phase": "abandoned"},
        })
        name = f"canonical/{record_file_name('state-v000001', _sha256(stranded))}"
        _write_immutable(root / name, stranded)
        repository = FilesystemProjectRepository.open(root)
        self.populate(repository)
        # Planning refuses a project whose owner never opened it for writing.
        for lock in repository.lock_paths():
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.touch(exist_ok=True)
        self.assertIn(name, plan_project_migration(root).orphan_paths)

        target = self.root / "migrated" / "interrupted-building"
        result = FilesystemProjectRepository.migrate_project_format(root, target)

        self.assertEqual((target / name).read_bytes(), stranded)
        self.assertIn(name, result.orphans)
        self.assertEqual(FilesystemProjectRepository.open(target).read_head().version, 0)

    def test_migration_carries_unknown_legacy_event_keys_forward(self) -> None:
        root = self.legacy_envelope("annotated-building")
        self.write_legacy_version(
            root, {"phase": "developed"},
            issued_at="2026-01-01T00:00:00Z", issued_by="an older build",
        )
        repository = FilesystemProjectRepository.open(root)
        self.populate(repository)
        target = self.root / "migrated" / "annotated-building"

        FilesystemProjectRepository.migrate_project_format(root, target)

        events = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((target / "events").glob("event-v000001-*.json"))
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["issued_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(events[0]["issued_by"], "an older build")
        self.assertEqual(events[0]["schema"], "ProjectEvent@2")

    def test_a_state_that_cannot_be_digested_leaves_no_half_written_target(self) -> None:
        """Every version is validated before the first target byte is written."""

        root = self.legacy_envelope("undigestible-building")
        self.write_legacy_version(root, {
            "schema": "CanonicalState@1", "project_id": "undigestible-building",
            "version": 99, "phase": "wrong identity",
        })
        repository = FilesystemProjectRepository.open(root)
        self.populate(repository)
        target = self.root / "migrated" / "undigestible-building"

        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(root, target)

        self.assertIn("canonical identity disagrees", str(raised.exception))
        self.assertFalse(target.exists(), "a failed migration leaves no target behind")

    def test_a_failure_after_the_envelope_removes_the_target_so_a_retry_starts_clean(self) -> None:
        repository = self.legacy_project()
        source = repository.layout.root
        target = self.root / "migrated" / "legacy-building"
        boom = OSError("the volume went away")
        with patch.object(FilesystemProjectRepository, "verify", side_effect=boom):
            with self.assertRaises(ProjectIntegrityError) as raised:
                FilesystemProjectRepository.migrate_project_format(source, target)
        self.assertIn("MIGRATION_FAILED", str(raised.exception))
        self.assertFalse(target.exists())
        # The retry is not refused by its own leftovers.
        result = FilesystemProjectRepository.migrate_project_format(source, target)
        self.assertEqual(result.target_format_version, CURRENT_FORMAT_VERSION)

    def test_migration_refuses_an_authored_base_no_version_publishes(self) -> None:
        root = self.legacy_envelope("stranded-building")
        repository = FilesystemProjectRepository.open(root)
        head = repository.read_head()
        repository.initialize_authored_inputs(
            expected_head=head, expected_record=None,
            authored_record={"schema": "StateRecord@1", "draft": "initial",
                             "base": {"project_id": "stranded-building", "version": 0,
                                      "state_sha256": "d" * 64}},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.populate(repository)
        target = self.root / "migrated" / "stranded-building"
        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(root, target)
        self.assertIn("MIGRATION_AUTHORED_BASE_UNKNOWN", str(raised.exception))
        self.assertFalse(target.exists())

    def test_the_two_key_and_flat_digest_shapes_are_found_and_blocked(self) -> None:
        """A scan that knew only the exact mapping would migrate past these."""

        repository = self.legacy_project()
        source = repository.layout.root
        head = repository.read_head()
        data = _json_bytes({
            "schema": "RetiredLaneNote@1",
            "branch": {"version": head.version, "state_sha256": head.state_sha256},
            "metadata": {"base_version": str(head.version),
                         "base_state_sha256": head.state_sha256},
        })
        note = (repository.layout.run("extra").records
                / record_file_name("retired-lane-note", _sha256(data)))
        _write_immutable(note, data)
        target = self.root / "migrated" / "legacy-building"

        with self.assertRaises(ProjectIntegrityError) as raised:
            FilesystemProjectRepository.migrate_project_format(source, target)

        message = str(raised.exception)
        self.assertIn("/branch", message)
        self.assertIn("/metadata/base_state_sha256", message)
        self.assertFalse(target.exists())

    def test_migration_ignores_interrupted_writer_leftovers(self) -> None:
        repository = self.legacy_project()
        source = repository.layout.root
        leftover = repository.layout.run("extra").records / f".x.json.{'a' * 32}.tmp"
        leftover.write_bytes(b"half a record\n")
        self.addCleanup(leftover.unlink)
        target = self.root / "migrated" / "legacy-building"

        FilesystemProjectRepository.migrate_project_format(source, target)

        self.assertFalse((target / leftover.relative_to(source)).exists())

    def test_the_receipt_accounts_for_every_file_the_migration_handled(self) -> None:
        """Exact lists, so a skipped lock or a stray .tmp cannot hide in a count."""

        repository = self.legacy_project()
        source = repository.layout.root
        leftover = repository.layout.run("extra").records / f".x.json.{'b' * 32}.tmp"
        leftover.write_bytes(b"half a record\n")
        self.addCleanup(leftover.unlink)
        target = self.root / "migrated" / "legacy-building"

        result = FilesystemProjectRepository.migrate_project_format(source, target)

        # The two run manifests are restated in place; the records whose run
        # base moved are renamed, and what names them is rewritten to follow.
        renamed = {row for row in result.rewritten if " -> " in row}
        in_place = sorted(set(result.rewritten) - renamed)
        self.assertEqual(in_place, [
            "design/branches.json", "runs/extra/run.json", "runs/first/run.json",
        ])
        self.assertEqual(len(renamed), 4, sorted(renamed))
        # Everything else the source retains, except the envelope, the two
        # advisory locks and the interrupted writer's leftover, is copied byte
        # for byte to the same name.
        copied = {
            path.relative_to(source).as_posix()
            for path in sorted(source.rglob("*")) if path.is_file()
        }
        skipped = {"project.json", "HEAD", "HEAD.lock", "design/branches.lock",
                   leftover.relative_to(source).as_posix()}
        touched = set(in_place) | {row.split(" -> ")[0] for row in renamed}
        expected = {
            name for name in copied - skipped - touched
            if not name.startswith(("canonical/", "events/"))
        }
        self.assertEqual(result.preserved_files, len(expected))
        for name in expected:
            self.assertEqual(
                (target / name).read_bytes(), (source / name).read_bytes(), name,
            )
        self.assertEqual(result.orphans, ())
        self.assertEqual(result.undecodable, ())

    def test_the_receipt_record_survives_export_and_restore(self) -> None:
        repository = self.legacy_project()
        target = self.root / "migrated" / "legacy-building"
        result = FilesystemProjectRepository.migrate_project_format(
            repository.layout.root, target,
        )
        self.assertIsNotNone(result.receipt)

        archive = self.root / "migrated.monkeyhub.zip"
        self.assertEqual(self.invoke(target, "--export-archive", str(archive))[0], 0)
        restored_root = self.root / "restored" / "legacy-building"
        self.assertEqual(
            self.invoke(restored_root, "--restore-archive", str(archive))[0], 0,
        )
        restored = FilesystemProjectRepository.open(restored_root)
        self.assertEqual(
            restored.load_json(result.receipt)["schema"], "ProjectFormatMigration@1",
        )
        self.assertEqual(
            (restored_root / result.receipt.relative_path).read_bytes(),
            (target / result.receipt.relative_path).read_bytes(),
        )

    # ---- the CLI consumer

    def invoke(self, root: Path, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--project", str(root), *args])
        return result, output.getvalue()

    def invoke_failing(self, root: Path, *args: str) -> tuple[int, str, str]:
        """A refusal answers through argparse: status 2, the reason on stderr."""

        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                main(["--project", str(root), *args])
        return int(raised.exception.code or 0), output.getvalue(), errors.getvalue()

    def test_cli_inspects_a_current_project_and_writes_nothing(self) -> None:
        root = self.current_project().layout.root
        before = self.fingerprint(root)
        result, output = self.invoke(root, "--inspect-format")
        self.assertEqual(result, 0)
        self.assertIn("current: project format 2 is current", output)
        self.assert_unchanged(root, before)

    def test_cli_plans_a_legacy_project_without_offering_an_upgrade(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        self.install_historical_record(repository, "retired-lane-note", embeds_base=False)
        before = self.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")
        self.assertEqual(result, 0)
        self.assertIn("supported_legacy (format 1)", output)
        self.assertIn("retired-lane-note", output)
        self.assertIn("UNREGISTERED", output)
        self.assertIn("preserved (byte for byte)", output)
        self.assertIn("preserves it byte for byte", output)
        self.assertIn("--export-archive", output)
        self.assertIn("No file in the project was created or changed", output)
        self.assertNotIn("upgradeable", output)
        self.assert_unchanged(root, before)

    def test_cli_fails_closed_on_a_format_this_build_cannot_read(self) -> None:
        root = self.current_project().layout.root
        manifest = root / "project.json"
        manifest.chmod(0o644)
        manifest.write_bytes(_json_bytes({
            "schema": "ArchFlowProject@1",
            "project_id": "current-building",
            "format_version": CURRENT_FORMAT_VERSION + 1,
        }))
        before = self.fingerprint(root)
        for flag in ("--inspect-format", "--plan-migration"):
            with self.subTest(flag=flag):
                result, output = self.invoke(root, flag)
                self.assertEqual(result, 2)
                self.assertIn("too_new", output)
                self.assert_unchanged(root, before)

    def test_cli_returns_nonzero_when_the_retained_closure_cannot_be_planned(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        self.corrupt_a_retained_record(repository)
        before = self.fingerprint(root)

        # The declared format is still readable, so inspection alone succeeds.
        self.assertEqual(self.invoke(root, "--inspect-format")[0], 0)

        result, output = self.invoke(root, "--plan-migration")
        self.assertEqual(result, 2)
        self.assertIn("could not be read completely", output)
        self.assertIn("was not inventoried", output)
        self.assert_unchanged(root, before)

    def test_cli_returns_nonzero_when_planning_would_change_the_project(self) -> None:
        root = self.legacy_envelope("untouched-building")
        before = self.fingerprint(root)
        result, output = self.invoke(root, "--plan-migration")
        self.assertEqual(result, 2)
        self.assertIn("HEAD.lock", output)
        self.assertIn("was not inventoried", output)
        self.assert_unchanged(root, before)

    def test_migrate_format_writes_the_target_and_a_receipt(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        before = self.fingerprint(root)
        target = self.root / "migrated" / "legacy-building"
        code, output = self.invoke(root, "--migrate-format", "--into", str(target))
        self.assertEqual(code, 0, output)
        self.assert_unchanged(root, before)
        migrated = FilesystemProjectRepository.open(target)
        records = sorted(
            (target / "runs" / MIGRATION_RUN_ID / "records").glob(f"{PROJECT_FORMAT_MIGRATION}-*.json")
        )
        self.assertEqual(len(records), 1)
        receipt = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "ProjectFormatMigration@1")
        self.assertEqual((receipt["project_id"], receipt["source_format_version"], receipt["target_format_version"]), ("legacy-building", 1, 2))
        self.assertEqual([row["version"] for row in receipt["versions"]], list(range(repository.read_head().version + 1)))
        self.assertIn("runs/first/run.json", receipt["rewritten"])
        self.assertIn("embedded_legacy_references", receipt)
        self.assertIn("preserved_files", receipt)
        self.assertEqual(receipt["source_head_sha256"], _sha256((root / "HEAD").read_bytes()))
        self.assertIn(str(target), output)
        self.assertIn(f"project://legacy-building/runs/{MIGRATION_RUN_ID}/records/", output)
        self.assertEqual(inspect_project_format(target).status, "current")
        # The receipt is inside the closure, so it travels with the project.
        closure = migrated.export_transfer(include_contents=False, include_all_runs=True)
        self.assertIn(
            records[0].relative_to(target).as_posix(),
            [row["path"] for row in closure["files"]],
        )
        # Running it again refuses: the target is used, and idempotence is the format itself.
        code, output, errors = self.invoke_failing(root, "--migrate-format", "--into", str(target))
        self.assertEqual(code, 2, output)
        self.assertIn("MIGRATION_TARGET_USED", errors)

    def test_migrate_format_requires_into_and_refuses_the_source_tree(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        code, output, errors = self.invoke_failing(root, "--migrate-format")
        self.assertEqual(code, 2, output)
        self.assertIn("--into", errors)
        code, output, errors = self.invoke_failing(
            root, "--migrate-format", "--into", str(root / "nested" / "legacy-building"),
        )
        self.assertEqual(code, 2, output)
        self.assertIn("MIGRATION_TARGET_NESTED", errors)

    def test_into_without_migrate_format_is_a_usage_error(self) -> None:
        root = self.legacy_project().layout.root
        code, _, errors = self.invoke_failing(root, "--plan-migration", "--into", str(root / "x"))
        self.assertEqual(code, 2)
        self.assertIn("--into is only meaningful with --migrate-format", errors)

    def test_cli_refuses_a_format_report_with_initialization_inputs(self) -> None:
        root = self.current_project().layout.root
        with self.assertRaises(SystemExit):
            self.invoke(root, "--inspect-format", "--state-record", str(root / "x.json"))

    def test_the_named_backup_restores_the_legacy_project_independently(self) -> None:
        """Acceptance 8: the #56 archive is the pre-migration checkpoint.

        The plan tells an operator to export an archive before a migration is
        considered. That archive restores into a separate directory and keeps
        the project at its own format. Restoring does not take the design lock,
        so the restored copy reports the same refusal as any project its owner
        has not opened; that limitation is stated rather than hidden.
        """

        repository = self.legacy_project()
        root = repository.layout.root
        plan = plan_project_migration(root)
        self.assertTrue(plan.migration_required)
        before = self.fingerprint(root)

        archive = self.root / "legacy-building.monkeyhub.zip"
        self.assertEqual(self.invoke(root, "--export-archive", str(archive))[0], 0)
        self.assert_unchanged(root, before)

        restored_root = self.root / "restored" / "legacy-building"
        self.assertEqual(
            self.invoke(restored_root, "--restore-archive", str(archive))[0], 0,
        )
        restored = FilesystemProjectRepository.open(restored_root)
        self.assertEqual(restored.read_head(), repository.read_head())
        inspection = inspect_project_format(restored_root)
        self.assertEqual(inspection.status, PROJECT_FORMAT_SUPPORTED_LEGACY)
        self.assertEqual(inspection.format_version, LEGACY_FORMAT_VERSION)

        refusal = plan_project_migration(restored_root)
        self.assertFalse(refusal.planned)
        self.assertIn("design/branches.lock", refusal.blockers[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
