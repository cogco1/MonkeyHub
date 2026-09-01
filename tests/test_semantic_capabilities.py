from __future__ import annotations

import copy
import unittest

from archflow.control.baseline import StageBaselineLevel, StageBaselineRole
from archflow.control.semantic_capabilities import (
    SemanticCapabilityPolicy,
    SemanticCapabilityPolicyError,
    SemanticDesignWorkItem,
    bind_semantic_rule_packs,
    compile_semantic_design_work_items,
    current_semantic_capability_policy,
    require_current_semantic_capability_policy,
    require_supported_semantic_capability_policy,
    _stair_policy_v1,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef


SHA_A = "a" * 64
SHA_B = "b" * 64


def branch(*, epoch: int = 3) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="semantic-policy-fixture",
            run_id="run-004",
            base=ProjectVersionRef(
                "semantic-policy-fixture",
                0,
                SHA_A,
            ),
        ),
        branch_id="candidate-a",
        epoch=epoch,
    )


class SemanticCapabilityPolicyTests(unittest.TestCase):
    def test_policy_round_trip_is_deterministic_and_has_no_authority(self) -> None:
        policy = current_semantic_capability_policy()
        self.assertEqual(2, policy.policy_version)
        self.assertEqual(2, policy.packs[0].pack_version)
        reloaded = SemanticCapabilityPolicy.from_dict(policy.to_dict())

        self.assertEqual(policy, reloaded)
        self.assertEqual(policy.policy_digest, reloaded.policy_digest)
        require_supported_semantic_capability_policy(reloaded)
        require_current_semantic_capability_policy(reloaded)
        for payload in (
            policy.to_dict(),
            *(pack.to_dict() for pack in policy.packs),
            *(
                rules.to_dict()
                for pack in policy.packs
                for rules in pack.stage_rules
            ),
        ):
            self.assertFalse(payload["design_authority"])
            self.assertFalse(payload["stage_acceptance_authority"])
            self.assertFalse(payload["persistence_authority"])
            self.assertFalse(payload["canonical_write_authority"])

    def test_stair_binding_is_exact_context_and_stage_specific(self) -> None:
        policy = current_semantic_capability_policy()
        expected_rule_counts = {
            StageBaselineLevel.PRE_GEOMETRY: 0,
            StageBaselineLevel.SPATIAL: 6,
            StageBaselineLevel.DEVELOPED: 13,
            StageBaselineLevel.COORDINATED: 15,
        }

        for level, expected_rule_count in expected_rule_counts.items():
            with self.subTest(level=level):
                bindings = bind_semantic_rule_packs(
                    policy=policy,
                    branch=branch(),
                    stage_id="stage-2",
                    stage_subject_digest=SHA_B,
                    component_ref="design-component:exterior-stair-east",
                    component_digest="c" * 64,
                    semantic_kind="exterior-stair-envelope",
                    baseline_level=level,
                )
                self.assertEqual(1, len(bindings))
                binding = bindings[0]
                self.assertEqual(expected_rule_count, len(binding.active_rule_ids))
                self.assertEqual(branch(), binding.branch)
                self.assertEqual(SHA_B, binding.stage_subject_digest)
                self.assertEqual("c" * 64, binding.component_digest)
                self.assertEqual(policy.policy_digest, binding.policy_digest)
                self.assertEqual(binding, type(binding).from_dict(binding.to_dict()))
                expected_roles = (
                    ()
                    if level is StageBaselineLevel.PRE_GEOMETRY
                    else (StageBaselineRole.VERTICAL_CIRCULATION,)
                )
                self.assertEqual(expected_roles, binding.mandatory_roles)

    def test_v1_remains_supported_for_replay_but_not_current_authoring(
        self,
    ) -> None:
        legacy = _stair_policy_v1()

        self.assertEqual(1, legacy.policy_version)
        self.assertEqual(1, legacy.packs[0].pack_version)
        self.assertEqual(
            legacy,
            require_supported_semantic_capability_policy(legacy),
        )
        with self.assertRaisesRegex(
            SemanticCapabilityPolicyError,
            "requires the current policy",
        ):
            require_current_semantic_capability_policy(legacy)

    def test_turn_work_item_is_exact_non_dischargeable_rule_work(self) -> None:
        binding = bind_semantic_rule_packs(
            policy=current_semantic_capability_policy(),
            branch=branch(),
            stage_id="stage-2",
            stage_subject_digest=SHA_B,
            component_ref="design-component:exterior-stair-east",
            component_digest="c" * 64,
            semantic_kind="exterior-stair-envelope",
            baseline_level=StageBaselineLevel.SPATIAL,
        )[0]
        item = compile_semantic_design_work_items(
            inventory_digest="d" * 64,
            bindings=(binding,),
        )[0]

        self.assertEqual(item, SemanticDesignWorkItem.from_dict(item.to_dict()))
        self.assertEqual("vertical-circulation", item.topic)
        self.assertEqual((item.ref, *binding.rule_refs), item.required_response_refs)
        payload = item.to_dict()
        self.assertNotIn("status", payload)
        self.assertNotIn("discharge", payload)
        for field in (
            "design_authority",
            "stage_acceptance_authority",
            "persistence_authority",
            "canonical_write_authority",
        ):
            self.assertFalse(payload[field])

    def test_non_stair_and_near_match_do_not_receive_stair_pack(self) -> None:
        policy = current_semantic_capability_policy()
        for semantic_kind in (
            "escalator-envelope",
            "monumental-ascent",
            "stairlike-screen",
            "podium-step-course",
        ):
            with self.subTest(semantic_kind=semantic_kind):
                self.assertEqual(
                    (),
                    bind_semantic_rule_packs(
                        policy=policy,
                        branch=branch(),
                        stage_id="stage-2",
                        stage_subject_digest=SHA_B,
                        component_ref="design-component:access",
                        component_digest="c" * 64,
                        semantic_kind=semantic_kind,
                        baseline_level=StageBaselineLevel.SPATIAL,
                    ),
                )

    def test_framework_stair_pack_cannot_be_deleted_or_rewritten(self) -> None:
        payload = copy.deepcopy(current_semantic_capability_policy().to_dict())
        payload["packs"] = []
        content = {
            key: value for key, value in payload.items() if key != "policy_digest"
        }
        from archflow.contracts.canonical import canonical_digest

        payload["policy_digest"] = canonical_digest(content)
        with self.assertRaises((TypeError, SemanticCapabilityPolicyError)):
            policy = SemanticCapabilityPolicy.from_dict(payload)
            require_supported_semantic_capability_policy(policy)

        rewritten = copy.deepcopy(
            current_semantic_capability_policy().to_dict()
        )
        rewritten["packs"][0]["stage_rules"][0]["rule_ids"] = [
            "weakened-rule"
        ]
        pack_content = {
            key: value
            for key, value in rewritten["packs"][0].items()
            if key != "pack_digest"
        }
        rewritten["packs"][0]["pack_digest"] = canonical_digest(pack_content)
        policy_content = {
            key: value
            for key, value in rewritten.items()
            if key != "policy_digest"
        }
        rewritten["policy_digest"] = canonical_digest(policy_content)
        with self.assertRaisesRegex(
            SemanticCapabilityPolicyError,
            "not a supported framework policy",
        ):
            require_supported_semantic_capability_policy(
                SemanticCapabilityPolicy.from_dict(rewritten)
            )


if __name__ == "__main__":
    unittest.main()
