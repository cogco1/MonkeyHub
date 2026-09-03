from __future__ import annotations

import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.runtime.artifact_library import load_neutral_building_package
from archflow.contracts.canonical import canonical_digest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "probes" / "test_pantheon"
RUN_ID = "pantheon-longitudinal-001"


def _destination(area: PersistenceArea) -> PersistenceDestination:
    if area in {
        PersistenceArea.RUN_RECORD,
        PersistenceArea.RUN_CANDIDATE,
        PersistenceArea.RUN_REVIEW,
    }:
        return PersistenceDestination(area, run_id=RUN_ID)
    return PersistenceDestination(area)


def _records(repository, run, area: PersistenceArea):
    result: dict[str, list[tuple[object, dict[str, object]]]] = {}
    for ref in repository.list_json(run=run, destination=_destination(area)):
        payload = repository.load_json(ref)
        result.setdefault(payload["schema"], []).append((ref, payload))
    return result


class PantheonLongitudinalAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = FilesystemProjectRepository.open(PROBE)
        cls.run_ref = cls.repository.load_run(RUN_ID)
        cls.records = _records(cls.repository, cls.run_ref, PersistenceArea.RUN_RECORD)
        cls.candidates = _records(
            cls.repository, cls.run_ref, PersistenceArea.RUN_CANDIDATE
        )
        cls.reviews = _records(cls.repository, cls.run_ref, PersistenceArea.RUN_REVIEW)
        cls.exports = _records(cls.repository, cls.run_ref, PersistenceArea.EXPORT)
        cls.manifest = cls.records["PantheonLongitudinalManifest@1"][0][1]

    def test_raw_request_evidence_approval_and_agent_recipe_remain_distinct(self) -> None:
        inputs = self.repository.list_json(
            run=self.run_ref,
            destination=PersistenceDestination(PersistenceArea.INPUT),
        )
        self.assertEqual(len(inputs), 1)
        request = self.repository.load_json(inputs[0])
        self.assertEqual(set(request), {"schema", "prompt"})
        self.assertFalse(any(character.isdigit() for character in request["prompt"]))

        invocation = self.records["PantheonArchitectInvocationRecord@1"][0][1]
        approval = self.records["PantheonTargetApproval@1"][0][1]
        site = self.records["PantheonSiteResourceDerivation@1"][0][1]
        self.assertEqual(invocation["provider_id"], "codex-agent-cli")
        self.assertEqual(invocation["model_id"], "gpt-5.6-sol")
        self.assertEqual(invocation["status"], "success_after_https_fallback")
        self.assertFalse(invocation["generation_authority"])
        self.assertFalse(invocation["canonical_write_authority"])
        self.assertEqual(approval["raw_request_ref"], inputs[0].uri)
        self.assertFalse(approval["historical_exactness_claimed"])
        self.assertEqual(site["base"], self.manifest["base"])
        self.assertEqual(set(site["source_refs"]), {
            inputs[0].uri,
            self.manifest["retrieved_evidence_ref"],
            self.manifest["architect_invocation_ref"],
            self.records["PantheonTargetApproval@1"][0][0].uri,
        })
        self.assertEqual(self.manifest["upstream_agent_round_count"], 4)

    def test_agent_semantic_tree_is_the_geometry_program_identity(self) -> None:
        invocation = self.records["PantheonArchitectInvocationRecord@1"][0][1]
        recipe = invocation["recipe"]
        program = self.records["CompiledGeometryProgram@2"][0][1]
        developed = self.records["DevelopedDesignState@1"][0][1]

        components = {item["id"]: item for item in recipe["components"]}
        roots = [item for item in components.values() if item["parent_id"] is None]
        self.assertEqual([item["id"] for item in roots], ["pantheon-root"])
        self.assertEqual(
            {item["component_id"] for item in developed["components"]},
            set(components),
        )
        self.assertEqual(
            {item["component_id"] for item in program["component_digests"]},
            set(components),
        )
        bound_components = {
            item["component_id"] for item in program["proposal"]["semantic_bindings"]
        }
        self.assertEqual(
            bound_components,
            {component_id for component_id, item in components.items() if item["parent_id"] is not None},
        )
        self.assertTrue(
            all(item["object_ids"] for item in program["proposal"]["semantic_bindings"])
        )
        self.assertEqual(
            canonical_digest(program),
            self.manifest["geometry_program_digest"],
        )
        self.assertGreaterEqual(len(recipe["alternatives"]), 2)
        self.assertTrue(
            all(item["selected_advice"] and item["rejected_advice"] for item in recipe["expert_trace"])
        )
        self.assertTrue(
            any(item["status"] == "conflicting" for item in recipe["unresolved"])
        )

    def test_selection_repair_aesthetics_and_hard_authority_do_not_collapse(self) -> None:
        portfolio = self.records["DesignOptionPortfolio@1"][0][1]
        revisions = [item[1] for item in self.candidates["PantheonCandidateRevision@1"]]
        by_status = {item["status"]: item for item in revisions}
        aesthetic = self.reviews["PantheonAestheticObservation@1"][0][1]
        hard = self.reviews["PantheonHardValidationReceipt@1"][0][1]

        self.assertEqual(len(portfolio["branches"]), 2)
        self.assertFalse(portfolio["automatic_winner"])
        self.assertEqual([item["kind"] for item in portfolio["transitions"]], ["select"])
        self.assertEqual(set(by_status), {"accepted", "rejected"})
        self.assertEqual(by_status["accepted"]["predecessor_ref"], self.manifest["rejected_revision_ref"])
        self.assertEqual(
            by_status["accepted"]["stable_component_ids"],
            by_status["rejected"]["stable_component_ids"],
        )
        self.assertEqual(by_status["accepted"]["stable_component_ids"], ["dome", "rotunda"])
        self.assertTrue(hard["passed"])
        self.assertEqual(hard["findings"], [])
        self.assertFalse(hard["canonical_write_authority"])
        self.assertFalse(aesthetic["hard_gate_authority"])
        self.assertFalse(aesthetic["canonical_write_authority"])
        self.assertIsNone(aesthetic["selected_winner"])

    def test_exact_neutral_package_and_complete_trace_reload_after_restart(self) -> None:
        package_ref, _ = self.exports["NeutralBuildingPackage@1"][0]
        package = load_neutral_building_package(self.repository, package_ref)
        payload = package.to_dict()
        self.assertEqual(package.package_digest, self.manifest["package_digest"])
        self.assertEqual(package.geometry_program.program_digest, self.manifest["geometry_program_digest"])
        self.assertEqual(package.scene.scene_digest, self.manifest["scene_digest"])
        self.assertEqual(len(package.render_set.views), 5)
        self.assertFalse(payload["generation_authority"])
        self.assertFalse(payload["execution_replay"])
        self.assertFalse(self.manifest["external_platform_execution"])
        self.assertFalse(self.manifest["execution_replay_claimed"])
        self.assertFalse(self.manifest["canonical_promoted"])
        self.assertEqual(self.repository.read_head().version, 0)

        reopened = FilesystemProjectRepository.open(PROBE)
        reopened_run = reopened.load_run(RUN_ID)
        reopened_records = _records(reopened, reopened_run, PersistenceArea.RUN_RECORD)
        reopened_exports = _records(reopened, reopened_run, PersistenceArea.EXPORT)
        self.assertEqual(
            reopened_records["PantheonLongitudinalManifest@1"][0][1],
            self.manifest,
        )
        reopened_package_ref = reopened_exports["NeutralBuildingPackage@1"][0][0]
        self.assertEqual(
            load_neutral_building_package(reopened, reopened_package_ref),
            package,
        )

    def test_pantheon_answers_stay_out_of_framework_and_probe_is_data_only(self) -> None:
        banned = (
            "PantheonArchitectRecipe",
            "pantheon-root",
            "portico_16_corinthian_rows_8_4_4",
            "43.3m diameter scaled",
        )
        for path in (ROOT / "archflow").rglob("*"):
            if path.suffix.lower() not in {".py", ".json", ".md"}:
                continue
            source = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, source, str(path))
        self.assertFalse(any(PROBE.rglob("*.py")))
        self.assertEqual(self.repository.verify().orphan_paths, ())


if __name__ == "__main__":
    unittest.main()
