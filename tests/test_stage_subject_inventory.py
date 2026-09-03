from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archflow.capabilities.visual_inventory import (
    VisualSourceDisposition,
    VisualSourceDispositionKind,
    compile_visual_evidence_inventory,
)
from archflow.contracts.canonical import canonical_digest
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
    StageBaselineRole,
)
from archflow.control.semantic_capabilities import (
    current_semantic_capability_policy,
)
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryError,
    StageSubjectRoleObligation,
)
from archflow.project.refs import ProjectRecordRef
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.runtime.component_index import ComponentIndex, ComponentIndexEntry
from archflow.runtime.stage_subject_inventory import (
    StageSubjectInventoryCompilationError,
    compile_stage_subject_inventory,
)
from tests.test_component_index import _sources
from archflow.state.site_context import SiteBounds
from archflow.state.spatial import (
    ComponentMaturity,
    DesignComponent,
    MassingVolume,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)


def _exact_sources():  # type: ignore[no-untyped-def]
    state, program, receipt, tree, context = _sources()
    from archflow.runtime.component_index import build_component_index

    index = build_component_index(
        current_state=state,
        geometry_program=program,
        control_tree=tree,
        control_context=context,
        lifecycle_receipt=receipt,
    )
    return state.selected_schematic.option.proposal, index, tree.branch


def _record_ref(branch, name: str, digest: str) -> ProjectRecordRef:  # type: ignore[no-untyped-def]
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
            f"{name}.json"
        ),
        sha256=digest,
    )


def _text_only_visual_inventory():  # type: ignore[no-untyped-def]
    return compile_visual_evidence_inventory(
        source_disposition=VisualSourceDisposition(
            kind=VisualSourceDispositionKind.TEXT_ONLY,
            source_refs=("evidence:text-only-visual-disposition",),
            authority_refs=("authority:architect-visual-disposition",),
        ),
        source_images=(),
        rois=(),
    )


def _obligations(proposal):  # type: ignore[no-untyped-def]
    roles = tuple(
        sorted(
            BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL],
            key=lambda role: role.value,
        )
    )
    return {
        component.component_id: tuple(
            StageSubjectRoleObligation(
                role=role,
                disposition=StageSubjectDisposition.REQUIRED,
                target_refs=(component.identity_ref,),
                evidence_refs=(
                    f"evidence:{component.component_id}:{role.value}",
                ),
                authority_refs=(
                    f"authority:{component.component_id}:{role.value}",
                ),
            )
            for role in roles
        )
        for component in proposal.components
    }


def _compile(  # type: ignore[no-untyped-def]
    proposal,
    index,
    branch,
    *,
    obligations=None,
    proposal_ref=None,
    index_ref=None,
):
    visual_inventory = _text_only_visual_inventory()
    return compile_stage_subject_inventory(
        inventory_id="stage-subjects-stage-2",
        branch=branch,
        stage_id="stage-2",
        stage_subject_ref="design-state:stage-2",
        stage_subject_digest=index.design_state_digest,
        baseline_level=StageBaselineLevel.SPATIAL,
        component_proposal=proposal,
        component_proposal_ref=(
            proposal_ref
            or _record_ref(
                branch,
                "spatial-option-proposal",
                "a" * 64,
            )
        ),
        component_index=index,
        component_index_ref=(
            index_ref
            or _record_ref(branch, "component-index", "b" * 64)
        ),
        visual_inventory=visual_inventory,
        visual_inventory_ref=_record_ref(
            branch,
            "visual-inventory",
            "c" * 64,
        ),
        semantic_policy=current_semantic_capability_policy(),
        semantic_policy_ref=_record_ref(
            branch,
            "semantic-capability-policy",
            "e" * 64,
        ),
        role_obligations=(
            obligations if obligations is not None else _obligations(proposal)
        ),
    )


def _with_roof(proposal, index):  # type: ignore[no-untyped-def]
    source = next(
        component
        for component in proposal.components
        if component.component_id == "coffers"
    )
    roof = replace(
        source,
        component_id="roof",
        semantic_kind="roof-assembly",
    )
    changed_proposal = replace(
        proposal,
        components=tuple(
            sorted(
                (
                    roof if item.component_id == source.component_id else item
                    for item in proposal.components
                ),
                key=lambda item: item.component_id,
            )
        ),
    )
    changed_index = replace(
        index,
        component_proposal_digest=changed_proposal.proposal_digest,
        entries=tuple(
            sorted(
                (
                    replace(item, component=roof)
                    if item.component_id == source.component_id
                    else item
                    for item in index.entries
                ),
                key=lambda item: item.component_id,
            )
        ),
    )
    return changed_proposal, changed_index


def _stair_sources():  # type: ignore[no-untyped-def]
    branch = BranchRef(
        run=RunRef(
            project_id="stage-stair-fixture",
            run_id="run-004",
            base=ProjectVersionRef(
                "stage-stair-fixture",
                0,
                "1" * 64,
            ),
        ),
        branch_id="candidate-a",
        epoch=3,
    )
    evidence = (
        "project://stage-stair-fixture/runs/run-004/branches/"
        "candidate-a/records/stair-basis.json",
    )
    components = (
        DesignComponent(
            component_id="building",
            parent_component_id=None,
            semantic_kind="building",
            intent="Own the exact semantic subject root.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("building-volume",),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="exterior-stair-east",
            parent_component_id="building",
            semantic_kind="exterior-stair-envelope",
            intent="Reserve the exact east vertical access path.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
    )
    proposal = SpatialOptionProposal(
        option_id="stair-stage-subject",
        label="Stair stage subject fixture",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(1.0, "square-meter", evidence),
        footprint_cells=((0, 0),),
        levels=(SpatialLevel("ground", 0, 4, evidence),),
        volumes=(
            MassingVolume(
                "building-volume",
                SiteBounds((0, 0, 0), (1, 4, 1)),
                ("ground",),
                evidence,
            ),
        ),
        zones=(
            SpatialZone(
                "main",
                ("program-node:main",),
                ("ground",),
                ("building-volume",),
                evidence,
            ),
        ),
        components=components,
        connections=(),
        constraint_responses=(),
        typology_hypothesis="Generic stair policy fixture",
        palette_refs=(),
        rationale="Exercise mandatory stair semantic dispatch.",
        responds_to_refs=("design-state:stage-2",),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )
    index = ComponentIndex(
        project_id=branch.run.project_id,
        run_id=branch.run.run_id,
        base=branch.run.base,
        design_state_digest="2" * 64,
        component_proposal_digest=proposal.proposal_digest,
        geometry_program_digest="3" * 64,
        control_tree_digest="4" * 64,
        control_context_digest="5" * 64,
        lifecycle_receipt_digest="6" * 64,
        control_target_node_ref="design-node:stage-2",
        entries=tuple(
            ComponentIndexEntry(
                component=component,
                geometry_object_ids=(),
                binding_ids=(),
                dependency_ids=(),
                task_ids=(),
                source_refs=evidence,
            )
            for component in components
        ),
        dependencies=(),
        tasks=(),
    )
    return proposal, index, branch


def _compile_stair(*, obligations=None, index=None):  # type: ignore[no-untyped-def]
    proposal, exact_index, branch = _stair_sources()
    selected_index = index or exact_index
    base = _obligations(proposal)
    visual_inventory = _text_only_visual_inventory()
    return compile_stage_subject_inventory(
        inventory_id="stage-stair-inventory",
        branch=branch,
        stage_id="stage-2",
        stage_subject_ref="design-state:stage-2",
        stage_subject_digest="2" * 64,
        baseline_level=StageBaselineLevel.SPATIAL,
        component_proposal=proposal,
        component_proposal_ref=_record_ref(
            branch,
            "stair-component-proposal",
            "7" * 64,
        ),
        component_index=selected_index,
        component_index_ref=_record_ref(
            branch,
            "stair-component-index",
            "8" * 64,
        ),
        visual_inventory=visual_inventory,
        visual_inventory_ref=_record_ref(
            branch,
            "stair-visual-inventory",
            "a" * 64,
        ),
        semantic_policy=current_semantic_capability_policy(),
        semantic_policy_ref=_record_ref(
            branch,
            "semantic-capability-policy",
            "9" * 64,
        ),
        role_obligations=obligations or base,
    )


class StageSubjectInventoryTests(unittest.TestCase):
    def test_stair_semantics_automatically_inject_mandatory_rule_pack(self) -> None:
        inventory = _compile_stair()
        stair = next(
            item
            for item in inventory.entries
            if item.component_id == "exterior-stair-east"
        )
        vertical = next(
            item
            for item in stair.role_obligations
            if item.role.value == "vertical_circulation"
        )

        self.assertIs(vertical.disposition, StageSubjectDisposition.REQUIRED)
        self.assertEqual((stair.identity_ref,), vertical.target_refs)
        self.assertEqual(1, len(inventory.semantic_rule_pack_bindings))
        binding = inventory.semantic_rule_pack_bindings[0]
        self.assertEqual(stair.identity_ref, binding.component_ref)
        self.assertIn("stair-integer-step-solution", binding.active_rule_ids)
        self.assertFalse(inventory.is_legacy_read_only)
        self.assertEqual(inventory, StageSubjectInventory.from_dict(inventory.to_dict()))

    def test_stair_mandatory_role_cannot_be_na_or_retargeted(self) -> None:
        proposal, _index, _branch = _stair_sources()
        base_obligations = _obligations(proposal)
        for disposition, target_refs in (
            (StageSubjectDisposition.NOT_APPLICABLE, ()),
            (StageSubjectDisposition.REQUIRED, ("design-component:building",)),
        ):
            with self.subTest(disposition=disposition):
                obligations = dict(base_obligations)
                obligations["exterior-stair-east"] = (
                    *obligations["exterior-stair-east"],
                    StageSubjectRoleObligation(
                        role=StageBaselineRole.VERTICAL_CIRCULATION,
                        disposition=disposition,
                        target_refs=target_refs,
                        evidence_refs=("evidence:caller-stair",),
                        authority_refs=("authority:caller-stair",),
                    ),
                )
                with self.assertRaisesRegex(
                    StageSubjectInventoryCompilationError,
                    "weakened or changed",
                ):
                    _compile_stair(obligations=obligations)

    def test_policy_replay_rejects_removed_binding_and_stale_state_index(self) -> None:
        inventory = _compile_stair()
        payload = copy.deepcopy(inventory.to_dict())
        payload["semantic_rule_pack_bindings"] = []
        content = {
            key: value
            for key, value in payload.items()
            if key != "inventory_digest"
        }
        payload["inventory_digest"] = canonical_digest(content)
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "differ from exact policy replay",
        ):
            StageSubjectInventory.from_dict(payload)

        _proposal, index, _branch = _stair_sources()
        stale_index = replace(index, design_state_digest="f" * 64)
        with self.assertRaisesRegex(
            StageSubjectInventoryCompilationError,
            "another stage subject state",
        ):
            _compile_stair(index=stale_index)

    def test_policy_replay_rejects_rehashed_cross_context_bindings(self) -> None:
        inventory = _compile_stair()
        building = next(
            item for item in inventory.entries if item.component_id == "building"
        )

        def _change_epoch(binding):  # type: ignore[no-untyped-def]
            binding["branch"]["epoch"] += 1

        def _change_stage(binding):  # type: ignore[no-untyped-def]
            binding["stage_id"] = "stage-3"

        def _change_subject(binding):  # type: ignore[no-untyped-def]
            binding["stage_subject_digest"] = "f" * 64

        def _retarget_component(binding):  # type: ignore[no-untyped-def]
            binding["component_ref"] = building.identity_ref

        for label, mutate in (
            ("branch-epoch", _change_epoch),
            ("stage", _change_stage),
            ("stage-subject", _change_subject),
            ("component", _retarget_component),
        ):
            with self.subTest(label=label):
                payload = copy.deepcopy(inventory.to_dict())
                binding = payload["semantic_rule_pack_bindings"][0]
                mutate(binding)
                binding["binding_digest"] = canonical_digest(
                    {
                        key: value
                        for key, value in binding.items()
                        if key != "binding_digest"
                    }
                )
                payload["inventory_digest"] = canonical_digest(
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "inventory_digest"
                    }
                )

                with self.assertRaisesRegex(
                    StageSubjectInventoryError,
                    "crossed exact stage context",
                ):
                    StageSubjectInventory.from_dict(payload)

    def test_round_trip_is_deterministic_and_retains_non_geometric_subjects(
        self,
    ) -> None:
        proposal, index, branch = _exact_sources()
        first = _compile(proposal, index, branch)
        second = _compile(proposal, index, branch)
        reloaded = StageSubjectInventory.from_dict(first.to_dict())

        self.assertEqual(first, second)
        self.assertEqual(first.inventory_digest, second.inventory_digest)
        self.assertEqual(first, reloaded)
        self.assertEqual(
            tuple(sorted(item.component_id for item in proposal.components)),
            first.component_ids,
        )
        building = next(
            item for item in first.entries if item.component_id == "building"
        )
        self.assertEqual((), building.geometry_object_ids)
        self.assertEqual((), building.binding_ids)
        self.assertEqual(
            BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL],
            frozenset(item.role for item in building.role_obligations),
        )
        for payload in (
            first.to_dict(),
            *(entry.to_dict() for entry in first.entries),
            *(
                obligation.to_dict()
                for entry in first.entries
                for obligation in entry.role_obligations
            ),
        ):
            self.assertFalse(payload["design_authority"])
            self.assertFalse(payload["stage_acceptance_authority"])
            self.assertFalse(payload["persistence_authority"])
            self.assertFalse(payload["canonical_write_authority"])

    def test_roof_cannot_be_dropped_from_the_obligation_mapping(self) -> None:
        proposal, index, branch = _exact_sources()
        proposal, index = _with_roof(proposal, index)
        obligations = _obligations(proposal)
        obligations.pop("roof")

        with self.assertRaisesRegex(
            StageSubjectInventoryCompilationError,
            "map every exact component",
        ):
            _compile(
                proposal,
                index,
                branch,
                obligations=obligations,
            )

    def test_record_and_semantic_digests_remain_distinct_for_p036_replay(self) -> None:
        proposal, index, branch = _exact_sources()
        proposal, index = _with_roof(proposal, index)
        exact_proposal_ref = _record_ref(
            branch,
            "spatial-option-proposal",
            "c" * 64,
        )
        exact_index_ref = _record_ref(
            branch,
            "component-index",
            "d" * 64,
        )
        shrunk_proposal = replace(
            proposal,
            components=tuple(
                item for item in proposal.components if item.component_id != "roof"
            ),
        )
        shrunk_index = replace(
            index,
            component_proposal_digest=shrunk_proposal.proposal_digest,
            entries=tuple(
                item for item in index.entries if item.component_id != "roof"
            ),
        )

        inventory = _compile(
            shrunk_proposal,
            shrunk_index,
            branch,
            proposal_ref=exact_proposal_ref,
            index_ref=exact_index_ref,
        )
        self.assertEqual(exact_proposal_ref, inventory.component_proposal_ref)
        self.assertEqual(exact_index_ref, inventory.component_index_ref)
        self.assertNotEqual(
            inventory.component_proposal_ref.sha256,
            inventory.component_proposal_digest,
        )
        self.assertNotEqual(
            inventory.component_index_ref.sha256,
            inventory.component_index_digest,
        )

    def test_cross_branch_record_and_cross_run_index_are_rejected(self) -> None:
        proposal, index, branch = _exact_sources()
        cross_branch_ref = ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=(
                f"runs/{branch.run.run_id}/branches/other-option/records/"
                "spatial-option-proposal.json"
            ),
            sha256=proposal.proposal_digest,
        )
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "exact stage branch",
        ):
            _compile(
                proposal,
                index,
                branch,
                proposal_ref=cross_branch_ref,
            )

        with self.assertRaisesRegex(
            StageSubjectInventoryCompilationError,
            "project/run/base",
        ):
            _compile(
                proposal,
                replace(index, run_id="run-002"),
                branch,
            )

    def test_role_dispositions_coverage_and_authority_fail_closed(self) -> None:
        proposal, index, branch = _exact_sources()
        role = next(
            iter(BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL])
        )
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "needs target_refs",
        ):
            StageSubjectRoleObligation(
                role=role,
                disposition=StageSubjectDisposition.REQUIRED,
                target_refs=(),
                evidence_refs=("evidence:required",),
                authority_refs=("authority:required",),
            )
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "cannot name target_refs",
        ):
            StageSubjectRoleObligation(
                role=role,
                disposition=StageSubjectDisposition.NOT_APPLICABLE,
                target_refs=("design-component:building",),
                evidence_refs=("evidence:not-applicable",),
                authority_refs=("authority:not-applicable",),
            )

        incomplete = _obligations(proposal)
        building_rows = incomplete["building"]
        incomplete["building"] = building_rows[:-1]
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "exactly cover baseline roles",
        ):
            _compile(
                proposal,
                index,
                branch,
                obligations=incomplete,
            )


if __name__ == "__main__":
    unittest.main()
