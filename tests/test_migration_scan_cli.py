from __future__ import annotations

import contextlib
import io
import unittest

from tests import test_project_format_migration as format_tests
from tools.create_project import _collect_legacy_version_references, main


class MigrationScanCliTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse #54's existing complete format fixtures rather than inventing a
        # second, weaker definition of a migratable retained project here.
        self.fixture = format_tests.ProjectFormatPlannerTests(
            methodName="test_current_project_is_detected_as_current_and_needs_no_migration"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @staticmethod
    def invoke(root, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--project", str(root), *args])
        return result, output.getvalue()

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
