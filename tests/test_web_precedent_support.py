from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.project.bootstrap import bootstrap_raw_request_project
from archive.tests.test_production_root_compiler import _context_and_options
from archive.tools.projects.monument_common.context import rebase_authoring_context
from archive.tools.projects.web_precedent.support import (
    WebPrecedentSupportError,
    load_rebased_authoring_context,
)


class WebPrecedentSupportTests(unittest.TestCase):
    def test_retained_context_is_loaded_and_rebased_to_exact_target_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "web-precedent-support"
            bootstrapped = bootstrap_raw_request_project(
                root,
                project_id="web-precedent-support",
                prompt="test retained context routing",
                run_id="source-run",
                synthetic_test=True,
            )
            repository = FilesystemProjectRepository.open(root)
            source_context = rebase_authoring_context(
                _context_and_options()[0],
                bootstrapped.run,
            )
            source_ref = repository.put_json(
                run=bootstrapped.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=bootstrapped.run.run_id,
                ),
                record_kind="production-authoring-context",
                payload=source_context.to_dict(),
            )
            target_run = repository.create_run("target-run")

            loaded, loaded_ref = load_rebased_authoring_context(
                repository,
                source_run_id=bootstrapped.run.run_id,
                target_run=target_run,
            )

            self.assertEqual(source_ref, loaded_ref)
            self.assertEqual(target_run, loaded.state.branch.run)
            self.assertEqual(target_run.project_id, loaded.program.project_id)
            self.assertEqual(target_run.run_id, loaded.program.run_id)
            self.assertEqual(target_run.base, loaded.program.base)
            self.assertEqual(target_run.base, loaded.site_context.base)
            self.assertEqual(target_run.base, loaded.build_policy.base)

    def test_missing_source_context_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "web-precedent-support"
            bootstrapped = bootstrap_raw_request_project(
                root,
                project_id="web-precedent-support",
                prompt="test missing retained context",
                run_id="source-run",
                synthetic_test=True,
            )
            repository = FilesystemProjectRepository.open(root)
            target_run = repository.create_run("target-run")

            with self.assertRaisesRegex(
                WebPrecedentSupportError,
                "exactly one production authoring context",
            ):
                load_rebased_authoring_context(
                    repository,
                    source_run_id=bootstrapped.run.run_id,
                    target_run=target_run,
                )


if __name__ == "__main__":
    unittest.main()
