"""Mechanical compiler for the exact semantic stage subject universe."""

from __future__ import annotations

from collections.abc import Mapping

from archive.archflow.capabilities.visual_inventory import (
    VisualEvidenceInventoryReceipt,
    VisualInventoryStatus,
)
from archive.archflow.control.baseline import StageBaselineLevel
from archive.archflow.control.semantic_capabilities import (
    SemanticCapabilityPolicy,
    SemanticRulePackBinding,
    bind_semantic_rule_packs,
    require_current_semantic_capability_policy,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectInventoryError,
    StageSubjectRoleObligation,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archive.archflow.runtime.component_index import ComponentIndex
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
    visual_inventory: VisualEvidenceInventoryReceipt,
    visual_inventory_ref: ProjectRecordRef,
    semantic_policy: SemanticCapabilityPolicy,
    semantic_policy_ref: ProjectRecordRef,
    role_obligations: Mapping[
        str,
        tuple[StageSubjectRoleObligation, ...],
    ],
) -> StageSubjectInventory:
    """Compile, never author, inventory entries from exact proposal/index rows.

    ``role_obligations`` may only map project obligations onto the mechanically
    derived component IDs.  Framework semantic rule packs are independently
    replayed and inject mandatory obligations before the entry is accepted;
    the caller cannot omit, weaken, or retarget them.
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
    if not isinstance(visual_inventory, VisualEvidenceInventoryReceipt):
        raise TypeError(
            "visual_inventory must be a VisualEvidenceInventoryReceipt"
        )
    if not isinstance(visual_inventory_ref, ProjectRecordRef):
        raise TypeError("visual_inventory_ref must be a ProjectRecordRef")
    if visual_inventory.status is not VisualInventoryStatus.PASS:
        raise StageSubjectInventoryCompilationError(
            "visual evidence inventory is incomplete"
        )
    if not isinstance(semantic_policy_ref, ProjectRecordRef):
        raise TypeError("semantic_policy_ref must be a ProjectRecordRef")
    require_current_semantic_capability_policy(semantic_policy)
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
    if component_index.design_state_digest != stage_subject_digest:
        raise StageSubjectInventoryCompilationError(
            "component index names another stage subject state"
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

    accepted_visual_components = {
        item.proposal_component_id: item
        for item in visual_inventory.accepted_component_identity_refs
    }
    if set(accepted_visual_components) != set(
        visual_inventory.visual_origin_proposal_component_ids
    ):
        raise StageSubjectInventoryCompilationError(
            "visual-origin proposal components lost their accepted hypotheses"
        )
    for component_id, accepted in accepted_visual_components.items():
        component = proposal_by_id.get(component_id)
        if component is None:
            raise StageSubjectInventoryCompilationError(
                "visual inventory names a foreign proposal component"
            )
        if accepted.component_identity_ref != component.identity_ref:
            raise StageSubjectInventoryCompilationError(
                "visual inventory component identity differs from proposal"
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

    all_bindings: list[SemanticRulePackBinding] = []
    compiled_obligations: dict[
        str,
        tuple[StageSubjectRoleObligation, ...],
    ] = {}
    for component_id in proposal_ids:
        component = proposal_by_id[component_id]
        bindings = bind_semantic_rule_packs(
            policy=semantic_policy,
            branch=branch,
            stage_id=stage_id,
            stage_subject_digest=stage_subject_digest,
            component_ref=component.identity_ref,
            component_digest=component.component_digest,
            semantic_kind=component.semantic_kind,
            baseline_level=baseline_level,
        )
        all_bindings.extend(bindings)
        supplied_by_role = {
            item.role: item for item in role_obligations[component_id]
        }
        if len(supplied_by_role) != len(role_obligations[component_id]):
            raise StageSubjectInventoryCompilationError(
                "caller role obligations duplicate a baseline role"
            )
        mandatory_by_role: dict[
            object,
            StageSubjectRoleObligation,
        ] = {}
        for binding in bindings:
            for role in binding.mandatory_roles:
                obligation = StageSubjectRoleObligation(
                    role=role,
                    disposition=StageSubjectDisposition.REQUIRED,
                    target_refs=(component.identity_ref,),
                    evidence_refs=(binding.basis_ref,),
                    authority_refs=(binding.authority_ref,),
                )
                existing = mandatory_by_role.get(role)
                if existing is not None and existing != obligation:
                    raise StageSubjectInventoryCompilationError(
                        "semantic rule packs conflict on one mandatory role"
                    )
                mandatory_by_role[role] = obligation
        for role, mandatory in mandatory_by_role.items():
            supplied = supplied_by_role.get(role)
            if supplied is not None and supplied != mandatory:
                raise StageSubjectInventoryCompilationError(
                    "caller weakened or changed a mandatory semantic role"
                )
            supplied_by_role[role] = mandatory
        compiled_obligations[component_id] = tuple(
            sorted(supplied_by_role.values(), key=lambda item: item.role.value)
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
            role_obligations=compiled_obligations[component_id],
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
        visual_inventory_ref=visual_inventory_ref,
        visual_inventory_digest=visual_inventory.inventory_digest,
        semantic_policy_ref=semantic_policy_ref,
        semantic_policy=semantic_policy,
        semantic_rule_pack_bindings=tuple(
            sorted(
                all_bindings,
                key=lambda item: (item.component_ref, item.pack_id),
            )
        ),
    )


def replay_stage_subject_inventory(
    *,
    inventory: StageSubjectInventory,
    component_proposal: SpatialOptionProposal,
    component_proposal_ref: ProjectRecordRef,
    component_index: ComponentIndex,
    component_index_ref: ProjectRecordRef,
    visual_inventory: VisualEvidenceInventoryReceipt | None = None,
    visual_inventory_ref: ProjectRecordRef | None = None,
) -> StageSubjectInventory:
    """Rebuild one archived inventory from independent exact sources.

    Current inventories replay the supported semantic policy and therefore
    cannot use their own obligations as the denominator authority.  V1
    inventories retain only their historical mechanical proposal/index join;
    callers may read them, while the StageExitArchiveBundle schema prevents
    them from authoring a new stage exit.
    """

    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be a StageSubjectInventory")
    if inventory.visual_inventory_ref is not None:
        if visual_inventory is None or visual_inventory_ref is None:
            raise StageSubjectInventoryCompilationError(
                "current inventory replay requires exact visual evidence"
            )
        return compile_stage_subject_inventory(
            inventory_id=inventory.inventory_id,
            branch=inventory.branch,
            stage_id=inventory.stage_id,
            stage_subject_ref=inventory.stage_subject_ref,
            stage_subject_digest=inventory.stage_subject_digest,
            baseline_level=inventory.baseline_level,
            component_proposal=component_proposal,
            component_proposal_ref=component_proposal_ref,
            component_index=component_index,
            component_index_ref=component_index_ref,
            visual_inventory=visual_inventory,
            visual_inventory_ref=visual_inventory_ref,
            semantic_policy=inventory.semantic_policy,
            semantic_policy_ref=inventory.semantic_policy_ref,
            role_obligations={
                entry.component_id: entry.role_obligations
                for entry in inventory.entries
            },
        )

    branch = inventory.branch
    run = branch.run
    if (
        component_index.project_id != run.project_id
        or component_index.run_id != run.run_id
        or component_index.base != run.base
        or component_index.component_proposal_digest
        != component_proposal.proposal_digest
        or component_index.design_state_digest
        != inventory.stage_subject_digest
    ):
        raise StageSubjectInventoryCompilationError(
            "legacy inventory crossed exact project/run/base/stage sources"
        )
    proposal_by_id = {
        item.component_id: item for item in component_proposal.components
    }
    index_by_id = {
        item.component_id: item for item in component_index.entries
    }
    archived_by_id = {
        item.component_id: item for item in inventory.entries
    }
    identities = tuple(sorted(proposal_by_id))
    if identities != tuple(sorted(index_by_id)) or identities != tuple(
        sorted(archived_by_id)
    ):
        raise StageSubjectInventoryCompilationError(
            "legacy inventory subject universe changed"
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
            role_obligations=archived_by_id[component_id].role_obligations,
        )
        for component_id in identities
    )
    return StageSubjectInventory(
        inventory_id=inventory.inventory_id,
        branch=branch,
        stage_id=inventory.stage_id,
        stage_subject_ref=inventory.stage_subject_ref,
        stage_subject_digest=inventory.stage_subject_digest,
        baseline_level=inventory.baseline_level,
        component_proposal_ref=component_proposal_ref,
        component_proposal_digest=component_proposal.proposal_digest,
        component_index_ref=component_index_ref,
        component_index_digest=component_index.index_digest,
        entries=entries,
        semantic_policy_ref=inventory.semantic_policy_ref,
        semantic_policy=inventory.semantic_policy,
        semantic_rule_pack_bindings=inventory.semantic_rule_pack_bindings,
    )


__all__ = [
    "StageSubjectInventoryCompilationError",
    "compile_stage_subject_inventory",
    "replay_stage_subject_inventory",
]
