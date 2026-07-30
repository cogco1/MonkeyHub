from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
)
from archflow.runtime import (
    DesignControllerCheckpoint,
    DesignControllerError,
    ProjectControllerArchiveAdapter,
)
from archflow.runtime.state_reducer import (
    canonical_state_to_dict,
    make_initialization_event,
)
from archflow.state import initialize_canonical_project
from archflow.state.design_state import DesignStateTree
from tests.test_design_controller import _checkpoint


def _bind_checkpoint(
    run,
    *,
    event_ref: str,
) -> DesignControllerCheckpoint:
    template, _ = _checkpoint()
    branch = BranchRef(
        run=run,
        branch_id=template.tree.branch.branch_id,
        epoch=template.tree.branch.epoch,
    )
    tree = DesignStateTree(
        branch=branch,
        nodes=tuple(
            replace(
                node,
                operational_state=replace(
                    node.operational_state,
                    branch=branch,
                ),
            )
            for node in template.tree.nodes
        ),
        interfaces=template.tree.interfaces,
    )
    target = tree.node(template.target_node_ref)
    return replace(
        template,
        tree=tree,
        maturity=replace(
            template.maturity,
            branch=branch,
            operational_state_digest=(
                target.operational_state.state_digest
            ),
        ),
        history_event_refs=(event_ref,),
    )


class DurableDesignStateResumeTests(unittest.TestCase):
    def test_restart_reloads_checkpoint_from_one_project_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-resume"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-resume"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-resume",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )

            adapter.event_log.append(initial_event)
            record = adapter.save_checkpoint(checkpoint)
            self.assertEqual(
                adapter.save_checkpoint(checkpoint),
                record,
            )

            del adapter
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            adapter = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint.tree.branch.branch_id,
                    epoch=checkpoint.tree.branch.epoch,
                ),
            )
            resumed = adapter.load_latest_checkpoint()

            self.assertEqual(resumed.record_ref, record)
            self.assertEqual(resumed.checkpoint, checkpoint)
            self.assertEqual(resumed.event_chain, (initial_event,))
            self.assertEqual(
                reopened.read_head(),
                initial_event.resulting_state,
            )
            self.assertEqual(reopened.verify().orphan_paths, ())

    def test_checkpoint_requires_exact_event_prefix_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-guard"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-guard"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-guard",
                initial_state=canonical_state_to_dict(sealed),
            )
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )
            adapter.event_log.append(initial_event)

            with self.assertRaisesRegex(
                DesignControllerError,
                "exact durable event chain",
            ):
                adapter.save_checkpoint(
                    replace(
                        checkpoint,
                        history_event_refs=("design-event:invented",),
                    )
                )

            other_branch = BranchRef(
                run=run,
                branch_id="other-option",
                epoch=0,
            )
            other_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=other_branch,
            )
            self.assertEqual(other_adapter.event_log.records(), ())
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_adapter.save_checkpoint(checkpoint)


if __name__ == "__main__":
    unittest.main()
