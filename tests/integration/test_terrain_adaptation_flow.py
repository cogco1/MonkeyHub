from __future__ import annotations

import unittest
from pathlib import Path

from archive.archflow.adapters.sandbox_render import SandboxRenderSet
from archive.archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertReceiptStatus,
    ExpertRegistry,
    ExpertSpec,
)
from archive.archflow.capabilities.terrain import build_terrain_expert_snapshot
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.realization.sandbox import HybridScene
from archive.archflow.runtime.terrain_adaptation import (
    TerrainAdaptationPlan,
    TerrainResponseOption,
    TerrainRelationshipReceipt,
    TerrainRetryStopReceipt,
    TerrainSelection,
    TerrainStateBindingReceipt,
    bind_terrain_context,
    compile_terrain_adaptation,
    guard_unchanged_terrain_retry,
    validate_terrain_relationship,
)
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopedDesignState
from archflow.contracts.canonical import canonical_digest
from archive.archflow.state.site_context import SiteContext


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "probes" / "p027-terrain-adaptation"
RUN_ID = "terrain-adaptation-001"


def _destination(area: PersistenceArea) -> PersistenceDestination:
    if area.value.startswith("run_"):
        return PersistenceDestination(area, run_id=RUN_ID)
    return PersistenceDestination(area)


def _records(repository, run, area: PersistenceArea):
    result: dict[str, list[tuple[object, dict[str, object]]]] = {}
    for ref in repository.list_json(
        run=run,
        destination=_destination(area),
    ):
        payload = repository.load_json(ref)
        result.setdefault(payload["schema"], []).append((ref, payload))
    return result


class TerrainAdaptationAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = FilesystemProjectRepository.open(PROBE)
        cls.run_ref = cls.repository.load_run(RUN_ID)
        cls.inputs = _records(cls.repository, cls.run_ref, PersistenceArea.INPUT)
        cls.records = _records(
            cls.repository,
            cls.run_ref,
            PersistenceArea.RUN_RECORD,
        )
        cls.reviews = _records(
            cls.repository,
            cls.run_ref,
            PersistenceArea.RUN_REVIEW,
        )
        cls.manifest = cls.records["TerrainAdaptationProbeManifest@2"][0][1]

    def test_observation_selection_and_geometry_authority_remain_separate(
        self,
    ) -> None:
        self.assertEqual(
            set(self.inputs),
            {
                "RawProjectRequest@1",
                "SiteReadApproval@1",
                "TerrainMeasurementInput@1",
            },
        )
        approval = self.inputs["SiteReadApproval@1"][0][1]
        measurement = self.inputs["TerrainMeasurementInput@1"][0][1]
        site = self.records["SiteContext@2"][0][1]
        decision = self.records["TerrainArchitectDecision@1"][0][1]
        plan = TerrainAdaptationPlan.from_dict(
            self.records["TerrainAdaptationPlan@2"][0][1]
        )

        self.assertTrue(approval["read_authorized"])
        self.assertFalse(approval["world_write_authority"])
        self.assertIsNone(measurement["selected_response"])
        self.assertFalse(measurement["geometry_authority"])
        self.assertEqual(site["ground_model"]["kind"], "uneven")
        self.assertEqual(
            [item["obligation_id"] for item in site["obligations"]],
            ["resolve.site.ground-response"],
        )
        self.assertEqual(decision["selected_option_id"], "terrain-option-support")
        self.assertFalse(decision["automatic_winner"])
        self.assertEqual(plan.selection.decision_ref, self.manifest["architect_decision_ref"])
        self.assertEqual(plan.selected_option.strategy_code, "elevated-support")
        self.assertEqual(
            plan.selected_option.resolved_obligation_ids,
            ("resolve.site.ground-response",),
        )
        self.assertEqual(
            set(plan.rejected_option_ids),
            {"terrain-option-grading", "terrain-option-relocation"},
        )
        self.assertEqual(
            plan.resolved_obligation_ids,
            ("resolve.site.ground-response",),
        )
        self.assertEqual(plan.open_obligation_ids, ())

    def test_experts_are_discoverable_advisers_not_a_hidden_controller(
        self,
    ) -> None:
        consultation = self.records["TerrainExpertConsultation@1"][0][1]
        review = self.reviews["TerrainAlternativeReview@1"][0][1]
        self.assertEqual(
            consultation["discovered_expert_ids"],
            [
                "expert.terrain.grading",
                "expert.terrain.siting",
                "expert.terrain.support",
            ],
        )
        self.assertTrue(
            all(item["status"] == "advice" for item in consultation["receipts"])
        )
        self.assertFalse(consultation["execution_order_prescribed"])
        self.assertFalse(consultation["automatic_winner"])
        self.assertFalse(consultation["design_authority"])
        self.assertEqual(review["selected_option_id"], "terrain-option-support")
        self.assertEqual(
            set(review["rejected_option_ids"]),
            {"terrain-option-grading", "terrain-option-relocation"},
        )

    def test_semantic_tree_is_the_geometry_program_identity(self) -> None:
        developed_payload = self.records["DevelopedDesignState@1"][0][1]
        developed = DevelopedDesignState.from_dict(developed_payload)
        geometry = self.records["CompiledGeometryProgram@2"][0][1]
        scene = HybridScene.from_dict(self.records["HybridSandboxScene@1"][0][1])

        semantic_ids = {
            item.component_id
            for item in developed.selected_schematic.option.proposal.components
        }
        developed_ids = {item.component_id for item in developed.components}
        compiled_ids = {
            item["component_id"] for item in geometry["component_digests"]
        }
        bound_ids = {
            item["component_id"]
            for item in geometry["proposal"]["semantic_bindings"]
        }
        self.assertEqual(
            semantic_ids,
            {
                "terrain-root",
                "site-ground",
                "foundation-supports",
                "building-platform",
            },
        )
        self.assertEqual(developed_ids, semantic_ids)
        self.assertEqual(compiled_ids, semantic_ids)
        self.assertEqual(bound_ids, semantic_ids - {"terrain-root"})
        self.assertTrue(
            all(
                item["object_ids"]
                for item in geometry["proposal"]["semantic_bindings"]
            )
        )
        self.assertEqual(canonical_digest(geometry), self.manifest["geometry_program_digest"])
        self.assertEqual(scene.scene_digest, self.manifest["scene_digest"])
        self.assertEqual(scene.geometry_program_digest, self.manifest["geometry_program_digest"])

    def test_exact_contacts_pass_and_unchanged_failed_plan_is_stopped(
        self,
    ) -> None:
        plan = TerrainAdaptationPlan.from_dict(
            self.records["TerrainAdaptationPlan@2"][0][1]
        )
        scene = HybridScene.from_dict(self.records["HybridSandboxScene@1"][0][1])
        receipts = [
            TerrainRelationshipReceipt.from_dict(payload)
            for _, payload in self.reviews["TerrainRelationshipReceipt@1"]
        ]
        accepted = next(item for item in receipts if item.passed)
        failed = next(item for item in receipts if not item.passed)
        persisted_stop = TerrainRetryStopReceipt.from_dict(
            self.reviews["TerrainRetryStopReceipt@1"][0][1]
        )

        self.assertEqual(len(accepted.interfaces), 8)
        self.assertEqual(accepted.findings, ())
        self.assertEqual(
            [item.code for item in failed.findings],
            ["terrain.interface.vertical_gap"],
        )
        self.assertEqual(accepted.plan_digest, plan.plan_digest)
        self.assertEqual(accepted.geometry_program_digest, scene.geometry_program_digest)
        self.assertEqual(accepted.scene_digest, scene.scene_digest)
        self.assertEqual(
            validate_terrain_relationship(plan, scene, accepted.interfaces),
            accepted,
        )
        self.assertEqual(guard_unchanged_terrain_retry(plan, failed), persisted_stop)

    def test_typed_state_reloads_after_restart_without_promotion(self) -> None:
        state = OperationalMarkovState.from_dict(
            self.records["OperationalMarkovState@3"][0][1]
        )
        site = SiteContext.from_dict(self.records["SiteContext@2"][0][1])
        binding = TerrainStateBindingReceipt.from_dict(
            self.records["TerrainStateBindingReceipt@1"][0][1]
        )
        render_set = SandboxRenderSet.from_dict(
            self.records["SandboxRenderSet@1"][0][1]
        )
        self.assertEqual(
            state.value_for_ref("fact:parameter:site-context-digest"),
            site.context_digest,
        )
        self.assertEqual(binding.result_state_digest, state.state_digest)
        self.assertEqual(len(render_set.views), 5)
        self.assertFalse(self.manifest["canonical_promoted"])
        self.assertFalse(self.manifest["external_platform_execution"])
        self.assertFalse(self.manifest["structural_engineering_proven"])
        self.assertEqual(self.repository.read_head().version, 0)

        reopened = FilesystemProjectRepository.open(PROBE)
        reopened_run = reopened.load_run(RUN_ID)
        reopened_records = _records(
            reopened,
            reopened_run,
            PersistenceArea.RUN_RECORD,
        )
        self.assertEqual(
            reopened_records["TerrainAdaptationProbeManifest@2"][0][1],
            self.manifest,
        )
        self.assertEqual(reopened.verify().orphan_paths, ())

    def test_current_compilers_reproduce_state_and_follow_only_explicit_selection(
        self,
    ) -> None:
        persisted_state = OperationalMarkovState.from_dict(
            self.records["OperationalMarkovState@3"][0][1]
        )
        site = SiteContext.from_dict(self.records["SiteContext@2"][0][1])
        persisted_binding = TerrainStateBindingReceipt.from_dict(
            self.records["TerrainStateBindingReceipt@1"][0][1]
        )
        persisted_plan = TerrainAdaptationPlan.from_dict(
            self.records["TerrainAdaptationPlan@2"][0][1]
        )
        seed = OperationalMarkovState(
            branch=persisted_state.branch,
            compiler_version="archflow.p027.seed@1",
            phase=DesignPhase.SITE_RESOURCE_COORDINATION.value,
            evidence_refs=(self.manifest["raw_request_ref"],),
        )
        rebound, binding = bind_terrain_context(seed, site)
        self.assertEqual(rebound, persisted_state)
        self.assertEqual(binding, persisted_binding)

        registry = ExpertRegistry()
        def advice(snapshot):
            return ExpertAdvice(
                summary="Project-scoped terrain strategy advice.",
                evidence_refs=tuple(
                    item.evidence_ref for item in snapshot.evidence
                ),
            )

        for expert_id in (
            "expert.terrain.grading",
            "expert.terrain.siting",
            "expert.terrain.support",
        ):
            registry.register(
                ExpertSpec(
                    expert_id=expert_id,
                    description="Project-scoped test expert.",
                    topics=frozenset({"terrain"}),
                    required_evidence_kinds=frozenset({"site_context"}),
                ),
                advice,
            )
        snapshot = build_terrain_expert_snapshot(rebound, site)
        discovered = registry.discover(snapshot)
        self.assertEqual(
            tuple(item.expert_id for item in discovered),
            (
                "expert.terrain.grading",
                "expert.terrain.siting",
                "expert.terrain.support",
            ),
        )
        self.assertTrue(
            all(
                registry.invoke(item.expert_id, snapshot).status
                is ExpertReceiptStatus.ADVICE
                for item in discovered
            )
        )
        self.assertEqual(
            compile_terrain_adaptation(
                rebound,
                site,
                persisted_plan.alternatives,
                persisted_plan.selection,
            ),
            persisted_plan,
        )

        context_ref = f"site-context:{site.context_digest}"
        unresolved = TerrainResponseOption(
            option_id="terrain-option-unresolved",
            expert_id="expert.terrain.support",
            strategy_code="defer-response",
            site_context_digest=site.context_digest,
            summary="Leave the ground response open pending further evidence.",
            tradeoffs=("Geometry compilation remains blocked.",),
            assumptions=(),
            resolved_obligation_ids=(),
            evidence_refs=(context_ref,),
        )
        unresolved_selection = TerrainSelection(
            selection_id="terrain-selection-unresolved-test",
            selected_option_id=unresolved.option_id,
            authority_id="authority.test",
            decision_ref="decision:terrain-unresolved-test",
            rationale="Test that the framework preserves an explicit unresolved choice.",
            evidence_refs=(context_ref,),
        )
        unresolved_plan = compile_terrain_adaptation(
            rebound,
            site,
            (*persisted_plan.alternatives, unresolved),
            unresolved_selection,
        )
        self.assertEqual(unresolved_plan.resolved_obligation_ids, ())
        self.assertEqual(
            unresolved_plan.open_obligation_ids,
            ("resolve.site.ground-response",),
        )

    def test_project_answer_stays_in_the_data_only_probe(self) -> None:
        banned = (
            "p027-terrain-adaptation",
            "terrain-support-scheme",
            "terrain-option-grading",
            "expert.terrain.grading",
            "elevated-support",
            "ground-ne",
            "support-sw",
        )
        for path in (ROOT / "archflow").rglob("*"):
            if path.suffix.lower() not in {".py", ".json", ".md"}:
                continue
            source = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, source, str(path))
        self.assertFalse(any(PROBE.rglob("*.py")))


if __name__ == "__main__":
    unittest.main()
