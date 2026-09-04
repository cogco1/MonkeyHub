from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.runtime.artifact_library import (
    ArtifactLibraryError,
    load_neutral_building_package,
    persist_neutral_building_package,
)
from archive.tests.test_artifact_library import neutral_package_fixture


_RETIRED_LANE_KINDS = (
    "a retired lane writes the record kinds this needs; put_json writes only "
    "kinds registered in archflow.project.record_kinds, and a kind no spine "
    "module writes, reads or names is not registered"
)


class SavedBuildReloadIntegrationTests(unittest.TestCase):
    @unittest.skip(_RETIRED_LANE_KINDS)
    def test_p036_export_reload_preserves_package_and_not_head_or_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "portfolio-project"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="portfolio-project",
                initial_state={"phase": "neutral-save-test"},
            )
            base = repository.read_head()
            run = repository.create_run("run-001", base=base)
            package = neutral_package_fixture(base=base)
            before_head = repository.read_head()

            ref = persist_neutral_building_package(
                repository,
                run=run,
                package=package,
                destination=PersistenceDestination(PersistenceArea.EXPORT),
            )

            self.assertTrue(ref.relative_path.startswith("exports/"))
            self.assertEqual(repository.read_head(), before_head)
            reopened = FilesystemProjectRepository.open(root)
            reloaded = load_neutral_building_package(reopened, ref)
            self.assertEqual(reloaded, package)
            self.assertEqual(reloaded.package_digest, package.package_digest)
            self.assertEqual(reopened.read_head(), before_head)
            self.assertFalse(reloaded.to_dict()["execution_replay"])
            self.assertFalse(reloaded.geometry_program.to_dict()["execution_replay"])

    def test_package_writer_rejects_non_export_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "portfolio-project"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="portfolio-project",
                initial_state={"phase": "neutral-save-test"},
            )
            run = repository.create_run("run-001")
            package = neutral_package_fixture(base=run.base)
            with self.assertRaisesRegex(ArtifactLibraryError, "only to the project export"):
                persist_neutral_building_package(
                    repository,
                    run=run,
                    package=package,
                    destination=PersistenceDestination(
                        PersistenceArea.RUN_RECORD,
                        run_id=run.run_id,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
