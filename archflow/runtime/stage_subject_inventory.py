"""Mechanical compiler for the exact semantic stage subject universe."""

from __future__ import annotations

from collections.abc import Mapping

from archflow.control.baseline import StageBaselineLevel
from archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectInventoryError,
    StageSubjectRoleObligation,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.runtime.component_index import ComponentIndex
from archflow.state.spatial import SpatialOptionProposal


class StageSubjectInventoryCompilationError(StageSubjectInventoryError):
    """Exact proposal/index sources cannot compile one subject inventory."""


def compile_stage_subject_inventory(
    *,
    inventory_id: str,
    branch: BranchRef,
    stage_id: str,
    stage_subject_ref: str,
    stage_subject_digest: str,
    baseline_level: StageBaselineLevel,
    component_proposal: SpatialOptionProposal,
    component_proposal_ref: ProjectRecordRef,
    component_index: ComponentIndex,
    component_index_ref: ProjectRecordRef,
    role_obligations: Mapping[
        str,
        tuple[StageSubjectRoleObligation, ...],
    ],
) -> StageSubjectInventory:
    """Compile, never author, inventory entries from exact proposal/index rows.

    ``role_obligations`` may only map obligations onto the mechanically derived
    component IDs.  It cannot introduce a component or omit one.
    """

    if not isinstance(branch, BranchRef):
        raise TypeError("branch must be a BranchRef")
    branch.run.base.require_digest()
    if not isinstance(baseline_level, StageBaselineLevel):
        raise TypeError("baseline_level must be a StageBaselineLevel")
    if not isinstance(component_proposal, SpatialOptionProposal):
        raise TypeError("component_proposal must be a SpatialOptionProposal")
    if not isinstance(component_index, ComponentIndex):
        raise TypeError("component_index must be a ComponentIndex")
    if not isinstance(component_proposal_ref, ProjectRecordRef):
        raise TypeError("component_proposal_ref must be a ProjectRecordRef")
    if not isinstance(component_index_ref, ProjectRecordRef):
        raise TypeError("component_index_ref must be a ProjectRecordRef")
    if not isinstance(role_obligations, Mapping):
        raise TypeError("role_obligations must be a component mapping")

    run = branch.run
    if (
        component_index.project_id != run.project_id
        or component_index.run_id != run.run_id
        or component_index.base != run.base
    ):
        raise StageSubjectInventoryCompilationError(
            "component index crossed the exact branch project/run/base"
        )
    if (
        component_index.component_proposal_digest
        != component_proposal.proposal_digest
    ):
        raise StageSubjectInventoryCompilationError(
            "component index names another component proposal"
        )

    proposal_by_id = {
        item.component_id: item for item in component_proposal.components
    }
    index_by_id = {
        item.component_id: item for item in component_index.entries
    }
    proposal_ids = tuple(sorted(proposal_by_id))
    index_ids = tuple(sorted(index_by_id))
    if proposal_ids != index_ids:
        raise StageSubjectInventoryCompilationError(
            "component proposal and index subject universes differ"
        )
    if any(
        index_by_id[component_id].component
        != proposal_by_id[component_id]
        for component_id in proposal_ids
    ):
        raise StageSubjectInventoryCompilationError(
            "component index changed an exact proposal component"
        )

    supplied_ids = tuple(sorted(role_obligations))
    if supplied_ids != proposal_ids:
        raise StageSubjectInventoryCompilationError(
            "role obligations must map every exact component once"
        )
    if any(
        not isinstance(component_id, str)
        or not isinstance(role_obligations[component_id], tuple)
        or any(
            not isinstance(item, StageSubjectRoleObligation)
            for item in role_obligations[component_id]
        )
        for component_id in proposal_ids
    ):
        raise TypeError(
            "role_obligations values must contain StageSubjectRoleObligation"
        )

    entries = tuple(
        StageSubjectInventoryEntry(
            component_id=component_id,
            identity_ref=proposal_by_id[component_id].identity_ref,
            parent_component_id=(
                proposal_by_id[component_id].parent_component_id
            ),
            semantic_kind=proposal_by_id[component_id].semantic_kind,
            component_digest=proposal_by_id[component_id].component_digest,
            geometry_object_ids=index_by_id[component_id].geometry_object_ids,
            binding_ids=index_by_id[component_id].binding_ids,
            role_obligations=role_obligations[component_id],
        )
        for component_id in proposal_ids
    )
    return StageSubjectInventory(
        inventory_id=inventory_id,
        branch=branch,
        stage_id=stage_id,
        stage_subject_ref=stage_subject_ref,
        stage_subject_digest=stage_subject_digest,
        baseline_level=baseline_level,
        component_proposal_ref=component_proposal_ref,
        component_proposal_digest=component_proposal.proposal_digest,
        component_index_ref=component_index_ref,
        component_index_digest=component_index.index_digest,
        entries=entries,
    )


__all__ = [
    "StageSubjectInventoryCompilationError",
    "compile_stage_subject_inventory",
]
