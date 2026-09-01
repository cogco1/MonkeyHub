from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
)
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryError,
    StageSubjectRoleObligation,
)
from archflow.project.refs import ProjectRecordRef
from archflow.runtime.stage_subject_inventory import (
    StageSubjectInventoryCompilationError,
    compile_stage_subject_inventory,
)
from tests.test_component_index import _sources


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


class StageSubjectInventoryTests(unittest.TestCase):
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

        payload = copy.deepcopy(_compile(proposal, index, branch).to_dict())
        payload["design_authority"] = True
        with self.assertRaisesRegex(
            StageSubjectInventoryError,
            "authority flags changed",
        ):
            StageSubjectInventory.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
