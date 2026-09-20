"""Keep the manual Stage measurement honest about retained geometry and failures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.monkeymonitor import run_stage_handoff_benchmark as benchmark


class StageBenchmarkTests(unittest.TestCase):
    def test_real_prepare_accepts_locks_reopens_builds_walls_and_flags_upstream_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Preparation must never instantiate the provider-owning Hub.
            with patch.object(benchmark.harness, "create_app", side_effect=AssertionError("prepare started a Hub/provider")):
                saved = benchmark.prepare(root)
            report = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
            self.assertTrue(all(report["checks"].values()), report["checks"])
            self.assertEqual(saved["live_model_calls"], 0)
            self.assertEqual(saved["history_turns"], 6)
            self.assertEqual(saved["orders"], [["continue", "stage"], ["stage", "continue"]])
            for pair in ("pair-1", "pair-2"):
                for mode in ("continue", "stage"):
                    repo = benchmark.harness.FilesystemProjectRepository.open(root / pair / mode / "projects" / benchmark.PROJECT_ID)
                    self.assertEqual(benchmark.harness.source_identity(repo), saved["source"])
            impact = report["impact"]
            # The baseline is a settled wall Stage, so every downstream claim
            # below is a new consequence of the condition edit alone.
            self.assertEqual(benchmark.impact_changes(impact["before"]),
                             {"changedRefs": [], "affectedRefs": [], "needsReviewRefs": [], "unresolvedImpactRefs": []})
            after = benchmark.impact_changes(impact["after"])
            self.assertEqual(after["changedRefs"], ["entity:entrance-condition"])
            self.assertIn("entity:hall-mass", after["affectedRefs"])
            self.assertIn("entity:wall-review", after["needsReviewRefs"])
            self.assertNotIn("entity:wall-review", after["changedRefs"])
            # The wall Stage exists only in the throwaway clone.
            self.assertEqual(impact["stage_counts"], {"impact_clone": 3, "measured_project": 2})
            clone = impact["impact_clone"]
            self.assertNotEqual(clone["project_dir"], clone["measured_project_dir"])
            self.assertEqual(Path(clone["measured_project_dir"]), root / "preflight" / benchmark.PROJECT_ID)
            self.assertTrue(Path(clone["project_dir"]).is_relative_to(root / "impact-check"))
            self.assertIsInstance(impact["clone_preparation_ms"], int)
            # The measured project kept the massing Stage it actually accepted.
            self.assertTrue(report["checks"]["no_wall_stage_accepted"])
            self.assertTrue(report["checks"]["measured_project_head_unchanged"])
            self.assertNotEqual(clone["test_wall_stage_ref"],
                                report["confirmed_context"]["confirmedStage"]["stageRef"])

    def test_upstream_checks_reject_a_baseline_that_already_listed_the_walls(self):
        def pack(changed, affected, needs_review):
            return {"confirmedStage": {"changes": {"changedRefs": changed, "affectedRefs": affected,
                                                   "needsReviewRefs": needs_review}}}

        settled = pack([], [], [])
        self.assertFalse(benchmark.upstream_impact_checks(settled, settled)["settled_baseline_stage"],
                         "an empty summary is not evidence of an accepted baseline")
        settled["confirmedStage"]["isSource"] = True
        reopened = pack(["entity:entrance-condition"], ["entity:hall-mass"],
                        ["entity:entrance-condition", "entity:hall-mass", "entity:wall-review"])
        self.assertTrue(all(benchmark.upstream_impact_checks(settled, reopened).values()))
        # The old probe compared against the pack that introduced the walls:
        # the mass was already affected and wall-review already needed review,
        # so neither review was a consequence of the condition edit.
        stale = pack(["entity:entrance-west", "entity:entrance-east", "entity:wall-review"],
                     ["entity:hall-mass"], ["entity:hall-mass", "entity:wall-review"])
        stale_checks = benchmark.upstream_impact_checks(stale, reopened)
        self.assertFalse(stale_checks["settled_baseline_stage"])
        self.assertFalse(stale_checks["wall_review_newly_needs_review"])
        self.assertFalse(stale_checks["retained_mass_newly_affected"])
        # An edit that also rewrites the wall Reading proves nothing either.
        edited = pack(["entity:entrance-condition", "entity:wall-review"], ["entity:hall-mass"],
                      ["entity:wall-review"])
        edited_checks = benchmark.upstream_impact_checks(settled, edited)
        self.assertFalse(edited_checks["only_the_condition_changed"])
        self.assertFalse(edited_checks["wall_review_not_itself_edited"])
        truncated = deepcopy(reopened)
        truncated["confirmedStage"]["omittedCounts"] = {"needsReviewRefs": 3}
        self.assertFalse(benchmark.upstream_impact_checks(settled, truncated)["no_omitted_summary"])

    def test_geometry_and_authored_checks_reject_wrong_mass_fake_walls_and_lost_condition(self):
        candidate = {"seatExecutionComplete": True, "objects": [
            {"producerOp": name, "lengthUnit": "meter", "upAxis": "Z-up", "bbox": {"min": minimum, "max": maximum}}
            for name, minimum, maximum in (
                ("hall-mass", [0, 0, 0], [4, 2, 3]),
                ("entrance-west", [0, -0.2, 0], [1.5, 0, 2.4]),
                ("entrance-east", [2.5, -0.2, 0], [4, 0, 2.4]))]}
        self.assertTrue(benchmark.geometry_ok(candidate, walls=True))
        wrong = deepcopy(candidate)
        wrong["objects"][1]["bbox"]["max"][0] = 1.6
        self.assertFalse(benchmark.geometry_ok(wrong, walls=True), "narrowed entrance was accepted")
        wrong = deepcopy(candidate)
        wrong["objects"][0]["bbox"]["max"][2] = float("nan")
        self.assertFalse(benchmark.geometry_ok(wrong, walls=True))
        before = benchmark.massing_payload()
        for parameter in before["parameters"]:
            parameter["lock_authority"] = "client"
        after = deepcopy(before)
        after["entities"].extend(benchmark.wall_edit()["entities"])
        self.assertTrue(all(benchmark.authored_checks(before, after).values()))
        lost_basis = deepcopy(after)
        original = next(row for row in lost_basis["entities"] if row.get("basis_refs"))
        original["basis_refs"] = []
        self.assertFalse(benchmark.authored_checks(before, lost_basis)["existing_entities_unchanged"])
        after["entities"][-1]["fields"]["subject_refs"].remove("entity:entrance-east")
        self.assertFalse(benchmark.authored_checks(before, after)["wall_review_scoped"])
        after["entities"][-2]["fields"]["producer"] = "prism"
        self.assertFalse(benchmark.authored_checks(before, after)["real_wall_producers"])
        after["parameters"][0]["value"] = 0.2
        self.assertFalse(benchmark.authored_checks(before, after)["existing_parameters_and_locks_unchanged"])

    def test_missing_failed_arms_are_retained_and_never_comparable_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark.summarize(root, {"orders": [["continue", "stage"], ["stage", "continue"]],
                                       "deterministic_preparation_ms": 123})
            result = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(len(result["pairs"]), 2)
            self.assertFalse(any(pair["comparable_successful_pair"] for pair in result["pairs"]))
            self.assertIn("raw_run_log", result["pairs"][0]["conditions"]["continue"])

    def test_totals_do_not_turn_unknown_usage_into_zero(self):
        totals = benchmark.total_metrics([
            {"metrics": {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 30, "model_calls": None}},
            {"metrics": {"input_tokens": 80, "cached_input_tokens": None, "output_tokens": 20, "model_calls": None}},
        ])
        self.assertEqual(totals["input_tokens"], 200)
        self.assertEqual(totals["output_tokens"], 50)
        self.assertIsNone(totals["cached_input_tokens"])
        self.assertIsNone(totals["model_calls"])


if __name__ == "__main__":
    unittest.main()
