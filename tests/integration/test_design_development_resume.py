from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.archflow.capabilities.design_development import (
    invalidate_developed_design,
)
from archflow.project.repository import FilesystemProjectRepository
from archive.archflow.runtime.development_controller import (
    DevelopmentControllerArchive,
    DevelopmentControllerCheckpoint,
)
from archive.archflow.runtime.state_reducer import canonical_state_to_dict
from archflow.state.developed_design import DevelopmentCoordinationStatus
from archflow.state.model import initialize_canonical_project
from archive.tests.test_design_development import (
    OPENING_REF,
    _coordinated_state,
)
from tests.test_design_portfolio import EVIDENCE, PROJECT_ID


class DesignDevelopmentResumeTests(unittest.TestCase):
    def test_reload_then_continue_from_exact_developed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / PROJECT_ID
            canonical = initialize_canonical_project(PROJECT_ID)
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id=PROJECT_ID,
                initial_state=canonical_state_to_dict(canonical),
            )
            run = repository.create_run("run-001")
            _, _, initial, coordinated = _coordinated_state(run)
            archive = DevelopmentControllerArchive(
                repository,
                run=run,
                coordination_id="selected-branch-a",
            )
            first = archive.save(
                DevelopmentControllerCheckpoint(
                    checkpoint_index=0,
                    predecessor_state_digest=None,
                    state=initial,
                )
            )
            second = archive.save(
                DevelopmentControllerCheckpoint(
                    checkpoint_index=1,
                    predecessor_state_digest=initial.state_digest,
                    state=coordinated,
                )
            )

            del archive
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            archive = DevelopmentControllerArchive(
                reopened,
                run=durable_run,
                coordination_id="selected-branch-a",
            )
            resumed = archive.load_latest()
            self.assertEqual(resumed.record_ref, second.record_ref)
            self.assertEqual(resumed.checkpoint.state, coordinated)

            invalidated = invalidate_developed_design(
                resumed.checkpoint.state,
                changed_schematic_refs=(OPENING_REF,),
                receipt_id="post-restart-opening-change",
                evidence_refs=(EVIDENCE,),
            )
            third = archive.save(
                DevelopmentControllerCheckpoint(
                    checkpoint_index=2,
                    predecessor_state_digest=(
                        resumed.checkpoint.state.state_digest
                    ),
                    state=invalidated,
                )
            )
            latest = archive.load_latest()

            self.assertNotEqual(first.record_ref, third.record_ref)
            self.assertEqual(latest.record_ref, third.record_ref)
            self.assertEqual(
                latest.checkpoint.state.coordination_status,
                DevelopmentCoordinationStatus.INVALIDATED,
            )
            self.assertEqual(
                latest.checkpoint.state.latest_invalidation.receipt_id,
                "post-restart-opening-change",
            )
            self.assertEqual(reopened.verify().orphan_paths, ())


if __name__ == "__main__":
    unittest.main()
