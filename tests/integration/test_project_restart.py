from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.runtime.state_reducer import (
    canonical_state_to_dict,
    make_initialization_event,
)
from archflow.state.model import initialize_canonical_project


class ProjectRestartIntegrationTests(unittest.TestCase):
    def test_p018_initial_event_and_p036_run_share_exact_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project("shared-base")
            event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "shared-base"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="shared-base",
                initial_state=canonical_state_to_dict(sealed),
            )

            self.assertEqual(repository.read_head(), sealed.ref)
            run = repository.create_run("run-001")
            self.assertEqual(run.base, sealed.ref)
            self.assertEqual(run.base, event.resulting_state)

            reopened = FilesystemProjectRepository.open(root)
            self.assertEqual(reopened.read_head(), sealed.ref)

    def test_close_reopen_preserves_accepted_state_and_run_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "restart-project"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="restart-project",
                initial_state={"phase": "raw-request"},
            )
            base = repository.read_head()
            run = repository.create_run("run-001", base=base)
            input_ref = repository.put_json(
                run=run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                record_kind="raw-request",
                payload={
                    "schema": "RawProjectRequest@1",
                    "prompt": "A project-specific request.",
                },
            )
            decision = repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_REVIEW,
                    run_id=run.run_id,
                ),
                record_kind="decision",
                payload={
                    "schema": "PromotionDecision@1",
                    "status": "accepted",
                    "project_id": run.project_id,
                    "run_id": run.run_id,
                    "checked_state": {
                        "project_id": base.project_id,
                        "version": base.version,
                        "state_sha256": base.require_digest(),
                    },
                    "candidate_ref": input_ref.uri,
                },
            )
            prepared = repository.prepare_transition(
                run=run,
                expected=base,
                replacement_state={
                    "phase": "request-recorded",
                    "request_ref": input_ref.uri,
                },
                decision_receipt=decision,
            )
            committed = repository.compare_and_swap(
                expected=prepared.expected,
                event=prepared.event,
                replacement=prepared.replacement,
            )

            del repository
            reopened = FilesystemProjectRepository.open(root)

            self.assertEqual(reopened.read_head(), committed)
            self.assertEqual(
                reopened.load_current_state(),
                {
                    "phase": "request-recorded",
                    "request_ref": input_ref.uri,
                },
            )
            self.assertEqual(reopened.load_json(input_ref)["schema"], "RawProjectRequest@1")
            self.assertEqual(reopened.verify().orphan_paths, ())


if __name__ == "__main__":
    unittest.main()
