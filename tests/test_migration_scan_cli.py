from __future__ import annotations

import contextlib
import io
import json
import unittest

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import record_file_name
from archflow.project.repository import _json_bytes, _sha256, _write_immutable
from tests import test_project_format_migration as format_tests
from tools.create_project import (
    RetainedVersionReference,
    UnreadableVersionReference,
    _collect_legacy_version_references,
    main,
)


class MigrationScanCliTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse #54's existing complete format fixtures rather than inventing a
        # second, weaker definition of a migratable retained project here.
        # "runTest" is the sentinel name unittest does not resolve, so this does
        # not depend on an unrelated test method keeping its name.
        self.fixture = format_tests.ProjectFormatPlannerTests(methodName="runTest")
        self.fixture.setUp()
        # The fixture's own doCleanups() swallows a failing cleanup when the
        # fixture was never run(), so register the temporary directory directly.
        self.addCleanup(self.fixture.temporary.cleanup)

    @staticmethod
    def invoke(root, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--project", str(root), *args])
        return result, output.getvalue()

    def install_retained_record(self, repository, body: dict) -> None:
        """Write one retained record carrying exactly the payload a test needs.

        The scan reads the retained closure, so a case about what a record holds
        has to put it in a real retained record rather than in a parsed literal.
        """

        data = _json_bytes({"schema": "StateRecord@1", "project_id": repository.layout.project_id, **body})
        _write_immutable(
            repository.layout.run("extra").records
            / record_file_name(STATE_RECORD, _sha256(data)),
            data,
        )

    def install_workspace_json_artifact(self, repository, body: dict) -> str:
        """Install a native JSON export under a run workspace, as a run would.

        A run workspace holds native exports and some are JSON; the project
        classifies them as artifacts it preserves byte-for-byte and never opens.
        """

        run = repository.create_run("native")
        artifact = repository.put_workspace_file(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_WORKSPACE, run_id="native",
            ),
            artifact_id="export",
            workspace_relative_path="export.json",
            media_type="application/json",
            source=io.BytesIO(json.dumps(body).encode("utf-8")),
        )
        repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id="native",
            ),
            record_kind=STATE_RECORD,
            payload={
                "schema": "StateRecord@1",
                "run": run.to_dict(),
                "model": {
                    "project_id": artifact.project_id,
                    "relative_path": artifact.relative_path,
                    "sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                },
            },
        )
        return artifact.relative_path

    def test_collector_accepts_only_exact_project_version_ref_shape(self) -> None:
        digest = "a" * 64
        payload = {
            "base": {"project_id": "building", "version": 3, "state_sha256": digest},
            "artifact": {
                "project_id": "building",
                "relative_path": "objects/x",
                "sha256": digest,
                "media_type": "application/json",
            },
            "lookalike": {
                "project_id": "building",
                "version": 3,
                "state_sha256": digest,
                "extra": True,
            },
            "list": [{"project_id": "building", "version": 1, "state_sha256": "b" * 64}],
        }

        references = _collect_legacy_version_references(payload, file="runs/r/run.json")

        self.assertEqual(
            [(reference.json_path, reference.version) for reference in references],
            [("/base", 3), ("/list/0", 1)],
        )

    def test_plan_migration_prints_real_legacy_reference_locations_without_writing(self) -> None:
        repository = self.fixture.legacy_project("legacy-scan")
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 0)
        self.assertIn(
            "Legacy ProjectVersionRef scan (exact shape only; not migration approval):",
            output,
        )
        self.assertRegex(output, r"found [1-9][0-9]* exact reference\(s\)")
        self.assertIn("HEAD /current -> version 0", output)
        self.assertIn("events/", output)
        self.assertIn(" /to -> version 0", output)
        self.assertIn("typed owner still has to confirm migration semantics", output)
        self.assertIn("No file in the project was created or changed.", output)
        self.fixture.assert_unchanged(root, before)
        self.assertEqual(repository.read_head().version, 0)

    def test_collector_reports_a_shape_match_it_cannot_read_rather_than_dropping_it(
        self,
    ) -> None:
        payload = {
            "short": {"project_id": "building", "version": 1, "state_sha256": "abc"},
            "flag": {"project_id": "building", "version": True, "state_sha256": "a" * 64},
            "unnamed": {"project_id": "", "version": 1, "state_sha256": "a" * 64},
        }

        found = _collect_legacy_version_references(payload, file="runs/r/run.json")

        self.assertEqual(
            [item.json_path for item in found], ["/short", "/flag", "/unnamed"],
        )
        self.assertTrue(
            all(isinstance(item, UnreadableVersionReference) for item in found)
        )
        self.assertFalse(
            any(isinstance(item, RetainedVersionReference) for item in found)
        )
        self.assertEqual(
            {item.json_path: item.detail for item in found},
            {
                "/short": "state_sha256 is not a 64-character lowercase sha-256",
                "/flag": "version is not a non-negative integer",
                "/unnamed": "project_id is not a non-empty string",
            },
        )
        # The identity stays readable even where a sibling field does not, so the
        # foreign-project check below still has something to compare.
        self.assertEqual(
            [item.project_id for item in found if item.json_path != "/unnamed"],
            ["building", "building"],
        )

    def test_plan_reports_an_unreadable_shape_match_without_refusing(self) -> None:
        repository = self.fixture.legacy_project("malformed-scan")
        self.install_retained_record(repository, {"base": {
            "project_id": "malformed-scan", "version": 2, "state_sha256": "not-a-digest",
        }})
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 0)
        self.assertIn("MALFORMED, unchecked: state_sha256 is not a 64-character", output)
        self.assertIn("none is counted above and none was interpreted", output)
        self.fixture.assert_unchanged(root, before)

    def test_a_readable_foreign_project_identity_refuses_the_scan(self) -> None:
        repository = self.fixture.legacy_project("foreign-scan")
        self.install_retained_record(repository, {"base": {
            "project_id": "someone-elses-project", "version": 0, "state_sha256": "c" * 64,
        }})
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 2)
        self.assertIn("Legacy ProjectVersionRef scan: REFUSED", output)
        self.assertIn("MIGRATION_REFERENCE_SCAN_FOREIGN_PROJECT", output)
        self.assertIn("'someone-elses-project'", output)
        self.fixture.assert_unchanged(root, before)

    def test_a_foreign_identity_refuses_even_when_another_field_is_unreadable(
        self,
    ) -> None:
        """One bad digest must not carry a foreign identity out as merely unreadable."""

        repository = self.fixture.legacy_project("foreign-malformed")
        self.install_retained_record(repository, {"base": {
            "project_id": "someone-elses-project", "version": 0, "state_sha256": "short",
        }})
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 2)
        self.assertIn("MIGRATION_REFERENCE_SCAN_FOREIGN_PROJECT", output)
        self.assertIn("'someone-elses-project'", output)
        self.fixture.assert_unchanged(root, before)

    def test_scan_states_how_many_retained_files_it_never_opened(self) -> None:
        repository = self.fixture.legacy_project("coverage-scan")
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 0)
        # The fixture retains two content-addressed artifacts; the scan opens
        # neither, and saying only how many files it did read would read as
        # coverage of the whole closure.
        self.assertIn("2 retained file(s) were not opened", output)
        self.assertIn("preserving the file does not carry it forward", output)
        self.assertIn("this scan does not show that every typed reference is mapped", output)
        self.fixture.assert_unchanged(root, before)

    def test_a_workspace_json_artifact_is_never_opened_by_the_scan(self) -> None:
        """A native JSON export is an artifact; its contents are not the project's."""

        repository = self.fixture.legacy_project("workspace-scan")
        relative_path = self.install_workspace_json_artifact(repository, {"ref": {
            "project_id": "another-building", "version": 7, "state_sha256": "c" * 64,
        }})
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        # Reading it would both claim coverage the scan does not have and refuse
        # a project the repository deliberately admits.
        self.assertEqual(result, 0)
        self.assertNotIn("REFUSED", output)
        self.assertNotIn("another-building", output)
        self.assertNotIn(relative_path, output)
        self.fixture.assert_unchanged(root, before)

    def test_current_format_plan_does_not_emit_legacy_scan(self) -> None:
        repository = self.fixture.current_project("current-scan")
        root = repository.layout.root
        before = self.fixture.fingerprint(root)

        result, output = self.invoke(root, "--plan-migration")

        self.assertEqual(result, 0)
        self.assertNotIn("Legacy ProjectVersionRef scan", output)
        self.fixture.assert_unchanged(root, before)


if __name__ == "__main__":
    unittest.main()
