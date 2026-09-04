from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, RunRef
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.runtime.component_index import (
    ComponentIndex,
    ComponentIndexError,
    build_component_index,
    compile_component_task_context,
    load_component_index_snapshot,
    persist_component_index_snapshot,
)
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    compile_semantic_geometry_lifecycle,
)
from archive.archflow.state.design_state import (
    ContextSliceCompiler,
    DesignStateTree,
)
from archflow.state.spatial import DesignComponent
from archive.tests.test_design_controller import _tree
from archive.tests.test_geometry_compiler import COMMITMENT
from archive.tests.test_production_root_compiler import _MemoryRepository
from archive.tests.test_semantic_geometry_lifecycle import (
    _design_state,
    _geometry_proposal,
    _initial,
)


def _current_semantic_geometry():  # type: ignore[no-untyped-def]
    coarse_state, coarse_program = _initial()
    shell_state = _design_state(1)
    shell = compile_semantic_geometry_lifecycle(
        transaction_id="index-shell",
        predecessor_state=coarse_state,
        current_state=shell_state,
        predecessor_proposal=coarse_state.selected_schematic.option.proposal,
        current_proposal=shell_state.selected_schematic.option.proposal,
        prior_program=coarse_program,
        geometry_proposal=_geometry_proposal(
            shell_state,
            coarse_program,
            stage=1,
        ),
        active_commitment_refs=(COMMITMENT,),
    )
    assert shell.geometry_program is not None
    detailed_state = _design_state(2)
    detailed = compile_semantic_geometry_lifecycle(
        transaction_id="index-detail",
        predecessor_state=shell_state,
        current_state=detailed_state,
        predecessor_proposal=shell_state.selected_schematic.option.proposal,
        current_proposal=detailed_state.selected_schematic.option.proposal,
        prior_program=shell.geometry_program,
        geometry_proposal=_geometry_proposal(
            detailed_state,
            shell.geometry_program,
            stage=2,
        ),
        revalidated_component_ids=("oculus",),
        active_commitment_refs=(COMMITMENT,),
    )
    assert detailed.receipt.status is SemanticGeometryLifecycleStatus.COMPILED
    assert detailed.geometry_program is not None
    return detailed_state, detailed.geometry_program, detailed.receipt


def _control(run: RunRef):  # type: ignore[no-untyped-def]
    original, nodes = _tree()
    branch = BranchRef(
        run=run,
        branch_id=original.branch.branch_id,
        epoch=original.branch.epoch,
    )
    tree = DesignStateTree(
        branch=branch,
        nodes=tuple(
            replace(
                item,
                operational_state=replace(
                    item.operational_state,
                    branch=branch,
                ),
            )
            for item in original.nodes
        ),
        interfaces=original.interfaces,
    )
    target_ref = next(
        item.ref for item in tree.nodes if item.path == nodes["grid"].path
    )
    return tree, ContextSliceCompiler().compile(
        tree,
        target_node_ref=target_ref,
    )


def _sources():  # type: ignore[no-untyped-def]
    state, program, receipt = _current_semantic_geometry()
    run = RunRef(state.project_id, state.run_id, state.base)
    tree, context = _control(run)
    return state, program, receipt, tree, context


def _index():  # type: ignore[no-untyped-def]
    state, program, receipt, tree, context = _sources()
    return build_component_index(
        current_state=state,
        geometry_program=program,
        control_tree=tree,
        control_context=context,
        lifecycle_receipt=receipt,
    )


class ComponentIndexTests(unittest.TestCase):
    def test_deterministic_rebuild_round_trip_and_semantic_geometry_join(
        self,
    ) -> None:
        first = _index()
        second = _index()
        reloaded = ComponentIndex.from_dict(first.to_dict())

        self.assertEqual(first, second)
        self.assertEqual(first.index_digest, second.index_digest)
        self.assertEqual(first, reloaded)
        surface = first.entry("primary-surface")
        self.assertEqual("building", surface.parent_component_id)
        self.assertEqual("detailed", surface.stage)
        self.assertEqual(("building-binding",), surface.binding_ids)
        self.assertTrue(surface.geometry_object_ids)
        self.assertEqual(("surface-to-material",), surface.dependency_ids)
        self.assertIn("observe-future-material-choice", surface.task_ids)
        self.assertEqual((), surface.component.unresolved_child_roles)
        self.assertFalse(first.to_dict()["composition_tree_authority"])
        self.assertFalse(first.to_dict()["control_tree_authority"])

    def test_component_local_context_is_bounded_and_includes_dependencies(
        self,
    ) -> None:
        index = _index()
        context = compile_component_task_context(
            index,
            component_id="primary-surface",
            expected_index_digest=index.index_digest,
            max_entries=4,
        )

        self.assertEqual(
            ("building", "coffers", "oculus", "primary-surface"),
            tuple(item.component_id for item in context.entries),
        )
        self.assertNotIn(
            "primary-support",
            {item.component_id for item in context.entries},
        )
        self.assertEqual(
            ("surface-to-material",),
            tuple(item.dependency_id for item in context.dependencies),
        )
        self.assertIn(
            "schematic-part:surface",
            context.explicit_dependency_refs,
        )
        self.assertIn(
            "observe-future-material-choice",
            {item.obligation_id for item in context.tasks},
        )
        with self.assertRaisesRegex(ComponentIndexError, "stale"):
            compile_component_task_context(
                index,
                component_id="primary-surface",
                expected_index_digest="0" * 64,
            )
        with self.assertRaisesRegex(ComponentIndexError, "exceeds"):
            compile_component_task_context(
                index,
                component_id="primary-surface",
                expected_index_digest=index.index_digest,
                max_entries=3,
            )

    def test_missing_contradictory_or_stale_sources_fail_closed(self) -> None:
        state, program, receipt, tree, context = _sources()
        with self.assertRaisesRegex(ComponentIndexError, "ownership"):
            build_component_index(
                current_state=state,
                geometry_program=replace(
                    program,
                    objects=program.objects[:-1],
                ),
                control_tree=tree,
                control_context=context,
                lifecycle_receipt=receipt,
            )
        stale_program = replace(
            program,
            proposal=replace(
                program.proposal,
                design_state_digest="b" * 64,
            ),
        )
        with self.assertRaisesRegex(ComponentIndexError, "stale"):
            build_component_index(
                current_state=state,
                geometry_program=stale_program,
                control_tree=tree,
                control_context=context,
                lifecycle_receipt=receipt,
            )
        _, unrelated_context = _control(RunRef(state.project_id, state.run_id, state.base))
        unrelated_context = replace(
            unrelated_context,
            tree_digest="c" * 64,
        )
        with self.assertRaisesRegex(ComponentIndexError, "control context"):
            build_component_index(
                current_state=state,
                geometry_program=program,
                control_tree=tree,
                control_context=unrelated_context,
                lifecycle_receipt=receipt,
            )

    def test_parentage_is_only_the_embedded_design_component(self) -> None:
        index = _index()
        entry = index.entry("coffers")
        payload = entry.to_dict()

        self.assertNotIn("parent_component_id", payload)
        self.assertEqual(
            entry.parent_component_id,
            entry.component.parent_component_id,
        )
        payload["parent_component_id"] = "building"
        with self.assertRaisesRegex(ComponentIndexError, "schema drifted"):
            type(entry).from_dict(payload)

    def test_p036_snapshot_is_optional_and_delete_rebuild_is_equivalent(
        self,
    ) -> None:
        index = _index()
        run = RunRef(index.project_id, index.run_id, index.base)
        repository = _MemoryRepository()
        destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )
        ref = persist_component_index_snapshot(
            repository,
            run=run,
            destination=destination,
            index=index,
        )
        loaded = load_component_index_snapshot(
            repository,
            ref,
            expected_index_digest=index.index_digest,
        )
        self.assertEqual(index, loaded)
        self.assertFalse(loaded.to_dict()["persistence_authority"])

        changed = copy.deepcopy(repository.records[ref])
        changed["control_context_digest"] = "d" * 64
        repository.records[ref] = changed
        with self.assertRaisesRegex(ComponentIndexError, "stale"):
            load_component_index_snapshot(
                repository,
                ref,
                expected_index_digest=index.index_digest,
            )

        repository.records.clear()  # Delete only the disposable test snapshot.
        rebuilt = _index()
        self.assertEqual(index.index_digest, rebuilt.index_digest)
        with self.assertRaisesRegex(ComponentIndexError, "run-record"):
            persist_component_index_snapshot(
                repository,
                run=run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                index=index,
            )


if __name__ == "__main__":
    unittest.main()
