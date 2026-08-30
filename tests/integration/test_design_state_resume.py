from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
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
    epoch: int | None = None,
    decision_context_refs: tuple[str, ...] = (),
) -> DesignControllerCheckpoint:
    template, _ = _checkpoint()
    branch = BranchRef(
        run=run,
        branch_id=template.tree.branch.branch_id,
        epoch=(
            template.tree.branch.epoch
            if epoch is None
            else epoch
        ),
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
        decision_context_refs=decision_context_refs,
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
            record = adapter.save_checkpoint(checkpoint)

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
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_adapter.load_checkpoint(record)

            other_run = repository.create_run("run-002")
            other_run_adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=BranchRef(
                    run=other_run,
                    branch_id=checkpoint.tree.branch.branch_id,
                    epoch=checkpoint.tree.branch.epoch,
                ),
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "another branch",
            ):
                other_run_adapter.load_checkpoint(record)

    def test_latest_checkpoint_filters_historical_branch_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-epoch-resume"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-epoch-resume"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-epoch-resume",
                initial_state=canonical_state_to_dict(sealed),
            )
            head_before = repository.read_head()
            run = repository.create_run("run-001")

            checkpoint_3 = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=3,
            )
            adapter_3 = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint_3.tree.branch,
            )
            adapter_3.event_log.append(initial_event)
            record_3 = adapter_3.save_checkpoint(checkpoint_3)
            historical_payload = repository.load_json(record_3)
            historical_payload["branch_epoch"] = 2
            historical_payload["legacy_extension"] = (
                "retained historical checkpoint field"
            )
            repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=run.run_id,
                    branch_id=checkpoint_3.tree.branch.branch_id,
                ),
                record_kind="design-controller-historical-legacy",
                payload=historical_payload,
            )

            checkpoint_4 = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
            )
            adapter_4 = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint_4.tree.branch,
            )
            record_4 = adapter_4.save_checkpoint(checkpoint_4)
            self.assertNotEqual(record_3, record_4)
            with self.assertRaisesRegex(
                DesignControllerError,
                "record identity changed",
            ):
                adapter_4.load_checkpoint(record_3)

            del adapter_3
            del adapter_4
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            resumed_4 = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint_4.tree.branch.branch_id,
                    epoch=4,
                ),
            ).load_latest_checkpoint()
            resumed_3 = ProjectControllerArchiveAdapter(
                reopened,
                branch=BranchRef(
                    run=durable_run,
                    branch_id=checkpoint_3.tree.branch.branch_id,
                    epoch=3,
                ),
            ).load_latest_checkpoint()

            self.assertEqual(record_4, resumed_4.record_ref)
            self.assertEqual(checkpoint_4, resumed_4.checkpoint)
            self.assertEqual(record_3, resumed_3.record_ref)
            self.assertEqual(checkpoint_3, resumed_3.checkpoint)
            self.assertEqual(head_before, reopened.read_head())

    def test_same_epoch_latest_checkpoint_ambiguity_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            canonical = initialize_canonical_project(
                "controller-epoch-ambiguity"
            )
            initial_event, sealed = make_initialization_event(
                canonical,
                actor_id="system",
            )
            root = Path(temporary) / "controller-epoch-ambiguity"
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id="controller-epoch-ambiguity",
                initial_state=canonical_state_to_dict(sealed),
            )
            head_before = repository.read_head()
            run = repository.create_run("run-001")
            checkpoint = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
            )
            alternate = _bind_checkpoint(
                run,
                event_ref=initial_event.event_id,
                epoch=4,
                decision_context_refs=("evidence://alternate",),
            )
            adapter = ProjectControllerArchiveAdapter(
                repository,
                branch=checkpoint.tree.branch,
            )
            adapter.event_log.append(initial_event)
            adapter.save_checkpoint(checkpoint)
            adapter.save_checkpoint(alternate)

            with self.assertRaisesRegex(
                DesignControllerError,
                "ambiguous latest checkpoint lineage",
            ):
                adapter.load_latest_checkpoint()
            self.assertEqual(head_before, repository.read_head())


if __name__ == "__main__":
    unittest.main()
