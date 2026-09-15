"""Read-only project-format inspection and dry-run migration planning.

Every fixture here is a disposable project built inside the test's own
temporary directory. Nothing reads a user project, and every test that plans a
migration compares the whole project directory, advisory lock files included,
before and after: planning a project must never create or change a file in it.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE, STATE_RECORD
from archflow.project.refs import ProjectVersionRef, record_file_name
from archflow.project.repository import (
    CURRENT_FORMAT_VERSION,
    FilesystemProjectRepository,
    LEGACY_FORMAT_VERSION,
    PROJECT_FORMAT_CURRENT,
    PROJECT_FORMAT_INVALID,
    PROJECT_FORMAT_SUPPORTED_LEGACY,
    PROJECT_FORMAT_TOO_NEW,
    ProjectIntegrityError,
    _json_bytes,
    _sha256,
    _write_immutable,
    inspect_project_format,
    plan_project_migration,
)
from tools.create_project import main


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
        snapshot_bytes = _json_bytes({
            "schema": "CanonicalSnapshot@1",
            "project_id": name,
            "version": 0,
            "parent": None,
            "state": {"phase": "design"},
        })
        snapshot_digest = _sha256(snapshot_bytes)
        snapshot_path = f"canonical/{record_file_name('state-v000000', snapshot_digest)}"
        _write_immutable(root / snapshot_path, snapshot_bytes)
        head_ref = ProjectVersionRef(name, 0, snapshot_digest)
        event_bytes = _json_bytes({
            "schema": "ProjectEvent@1",
            "project_id": name,
            "event_type": "project.initialized",
            "decision": "accepted",
            "run_id": None,
            "from": None,
            "to": head_ref.to_dict(),
            "previous_event": None,
            "decision_receipt": None,
        })
        event_digest = _sha256(event_bytes)
        event_path = f"events/{record_file_name('event-v000000', event_digest)}"
        _write_immutable(root / event_path, event_bytes)
        _write_immutable(
            root / "HEAD",
            _json_bytes({
                "schema": "ProjectHead@1",
                "project_id": name,
                "current": head_ref.to_dict(),
                "snapshot": {"relative_path": snapshot_path, "sha256": snapshot_digest,
                             "media_type": "application/json"},
                "event": {"relative_path": event_path, "sha256": event_digest,
                          "media_type": "application/json"},
            }),
        )
        return root

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
    ) -> None:
        """Write a retained record whose kind this build no longer registers.

        Archived lanes left records like this behind; ``put_json`` refuses to
        write one now, but readers must keep them and a planner must not drop
        them silently.
        """

        data = _json_bytes({
            "schema": "RetiredLaneNote@1",
            "project_id": repository.layout.project_id,
            "base": repository.read_head().to_dict(),
        })
        _write_immutable(
            repository.layout.run("extra").records / record_file_name(kind, _sha256(data)),
            data,
        )

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
        self.assertIn("no project-format migrator is implemented for 1 -> 2", plan.blockers[0])
        reported = " ".join(
            (plan.inspection.detail, *plan.required_transformations, *plan.blockers)
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

    def test_unknown_retained_record_is_surfaced_as_a_blocker(self) -> None:
        repository = self.legacy_project()
        root = repository.layout.root
        self.install_historical_record(repository, "retired-lane-note")
        before = self.fingerprint(root)

        plan = plan_project_migration(root)
        unknown = [entry for entry in plan.inventory if entry.registered is False]
        self.assertEqual([entry.kind for entry in unknown], ["retired-lane-note"])
        self.assertEqual(unknown[0].schema, "RetiredLaneNote@1")
        blocker = [line for line in plan.blockers if "retired-lane-note" in line]
        self.assertEqual(len(blocker), 1)
        self.assertIn("RetiredLaneNote@1", blocker[0])
        self.assertIn("no typed owner", blocker[0])
        self.assert_unchanged(root, before)

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
        self.install_historical_record(repository, "retired-lane-note")
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
        self.assertEqual(len([line for line in plan.blockers if "retired-lane-note" in line]), 2)
        self.assert_unchanged(repository.layout.root, before)

    def test_the_plan_never_claims_to_have_analyzed_record_references(self) -> None:
        """Record payloads are not parsed, so no reference count is reported.

        A retained record embeds artifact references that share a key set with
        a record reference. Counting those by shape would be a guess, so the
        plan defers every payload question to that record's typed owner.
        """

        plan = plan_project_migration(self.legacy_project().layout.root)
        reported = (*plan.required_transformations, *plan.blockers)
        for line in reported:
            self.assertNotRegex(line, r"\d+ (typed )?(record|project-version) reference")
        deferred = [
            line for line in plan.required_transformations
            if "restated by its own typed owner" in line
        ]
        self.assertEqual(len(deferred), 1)
        self.assertIn("4 retained record(s)", deferred[0])
        unanalyzed = [line for line in plan.blockers if "does not parse record payloads" in line]
        self.assertEqual(len(unanalyzed), 1)
        self.assertIn("unanalyzed here", unanalyzed[0])

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

    # ---- the CLI consumer

    def invoke(self, root: Path, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--project", str(root), *args])
        return result, output.getvalue()

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
        self.install_historical_record(repository, "retired-lane-note")
        before = self.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")
        self.assertEqual(result, 0)
        self.assertIn("supported_legacy (format 1)", output)
        self.assertIn("retired-lane-note", output)
        self.assertIn("UNREGISTERED", output)
        self.assertIn("no project-format migrator is implemented for 1 -> 2", output)
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
