"""Independent four-arm, failure-denominator and cold-binding tests for GH-173.

Every provider here is a labeled deterministic test double. These tests establish
mechanics, not performance or architectural quality of any language model.
"""

from copy import deepcopy
from dataclasses import asdict, replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from archflow.contracts.canonical import canonical_json_bytes
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.project.repository import FilesystemProjectRepository, ProjectIntegrityError
from archflow.state.state_record import StateRecord, StateRecordOperator
from .benchmark import ScriptedProvider, run_batch, summarize
from .fixture import checks, final_assessment
from .harness import ARMS, Budget, adjudicate, replay, run_trial


class ObservedProvider(ScriptedProvider):
    def __init__(self, scenario="wrong_entry", *, fail_at=None, failure="timeout", raise_at=None,
                 missing_cost=False, cost=None):
        super().__init__(scenario)
        self.prompts = []
        self.options = []
        self.fail_at, self.failure, self.raise_at = fail_at, failure, raise_at
        self.missing_cost, self.cost = missing_cost, cost

    def call(self, prompt, schema, *, timeout_seconds, max_cost_usd):
        self.prompts.append(deepcopy(prompt))
        self.options.append({"timeout_seconds": timeout_seconds, "max_cost_usd": max_cost_usd})
        if len(self.prompts) == self.raise_at:
            raise OSError("private transport text must not enter experiment artifacts")
        result = super().call(prompt, schema, timeout_seconds=timeout_seconds, max_cost_usd=max_cost_usd)
        if len(self.prompts) == self.fail_at:
            return replace(result, answer=None, outcome=self.failure,
                           usage={key: None for key in result.usage})
        if self.missing_cost:
            return replace(result, usage={**result.usage, "api_equivalent_cost_usd": None})
        if self.cost is not None:
            return replace(result, usage={**result.usage, "api_equivalent_cost_usd": self.cost})
        return result


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="checkpoint-critique-test-")
        self.addCleanup(self.directory.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(self.directory.name) / "project", project_id="checkpoint-critique", initial_state={},
        )
        self.serial = 0

    def trial(self, arm="A", scenario="wrong_entry", *, provider=None, budget=Budget()):
        self.serial += 1
        return run_trial(self.repository, f"trial-{self.serial:02d}", arm,
                         provider if provider is not None else ScriptedProvider(scenario), budget)

    def record(self, reference):
        return StateRecord.from_dict(self.repository.load_json(ProjectRecordRef.from_dict(reference)))

    def artifact(self, reference):
        return self.repository.load_json(ProjectRecordRef(reference["project_id"], reference["relative_path"],
                                                           reference["sha256"], reference["media_type"]))

    def rewritten_report(self, report):
        self.serial += 1
        name = f"altered-report-{self.serial:02d}"
        run = RunRef.from_dict(report["run"])
        return asdict(self.repository.put_workspace_file(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run.run_id),
            artifact_id=name, workspace_relative_path=f"checkpoint-critique/{name}.json",
            media_type="application/json", source=BytesIO(canonical_json_bytes(report)),
        ))

    def test_four_arms_measure_actual_early_vs_late_propagation_and_repairs(self):
        rows = [self.trial(arm) for arm in ARMS]
        for report in rows:
            with self.subTest(arm=report["arm"]):
                late = report["arm"] in ("A", "C")
                self.assertTrue(report["complete"])
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["metrics"]["introduced_errors"], 1)
                self.assertEqual(report["metrics"]["detected_errors"], 1)
                self.assertEqual(report["propagation"][0]["depth"], 3 if late else 0)
                self.assertEqual(report["propagation"][0]["observed_depth_lower_bound"], 3 if late else 0)
                repair = report["repairs"][0]
                self.assertEqual(repair["actual_downstream_changed"],
                                 ["parameter:door_x", "parameter:gallery_x", "parameter:threshold_x"] if late else [])
                self.assertEqual(len(repair["changed_existing_refs"]), 4 if late else 1)
                self.assertEqual(report["metrics"]["repair_closure_size"], 3 if late else 0)
                self.assertEqual(report["metrics"]["late_revisions"], int(late))
                self.assertEqual(report["metrics"]["provider_calls"], 5 if late else 7)
                self.assertEqual(report["metrics"]["discarded_operations"], 2 if late else 0)
                self.assertTrue(report["head_unchanged"])
                self.assertEqual(report["final_assessment"], final_assessment(self.record(report["final_ref"])))
        grouped = summarize(rows)
        self.assertTrue(all(item["trials"] == item["completed"] == 1 for item in grouped.values()))

    def test_separate_critic_has_fresh_context_and_self_review_sees_public_history(self):
        policies = []
        for arm in ARMS:
            provider = ObservedProvider()
            report = self.trial(arm, provider=provider)
            reviews = [prompt for prompt in provider.prompts if prompt["task"] == "review"]
            self.assertTrue(reviews)
            for prompt in reviews:
                self.assertEqual(bool(prompt["public_history"]), arm in ("A", "B"))
                self.assertNotIn("final_assessment", prompt)
                policies.append(prompt["policy"])
            self.assertEqual(len(provider.prompts), len(report["calls"]))
        self.assertTrue(all(policy == policies[0] for policy in policies))

    def test_valid_unknown_and_false_veto_cannot_hard_block(self):
        for arm in ("B", "D"):
            report = self.trial(arm, "false_veto")
            self.assertTrue(report["complete"])
            self.assertEqual(report["metrics"]["unsupported_hard_block_requests"], 2)
            self.assertEqual(report["metrics"]["false_objections"], 2)
            self.assertEqual(report["metrics"]["false_hard_blocks"], 0)
            self.assertEqual(report["repairs"], [])
            self.assertFalse(any(event["outcome"] == "hard_block" for event in report["trajectory"]))
            self.assertEqual(report["final_assessment"]["unavailable"], ["structural_analysis"])

    def test_actual_invariant_violation_blocks_each_attempt_and_keeps_source(self):
        report = self.trial("D", "invariant")
        self.assertEqual(report["status"], "round_exhausted")
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["calls"]), 2)
        self.assertEqual([event["outcome"] for event in report["trajectory"]], ["hard_block", "hard_block"])
        self.assertEqual(report["final_ref"], report["initial_ref"])
        self.assertTrue(all("protected" in event["checks"][0]["reason"] for event in report["trajectory"]))
        self.assertEqual(report["metrics"]["introduced_errors"], 0)

    def test_unknown_capability_and_semantic_preference_are_not_hard_blocks(self):
        report = self.trial("D", "valid")
        event = next(event for event in report["trajectory"] if "binding" in event)
        candidate = self.record(event["binding"]["candidate"])
        challenge = {"binding": event["binding"], "requested_checks": ["structural_analysis", "semantic_detail"],
                     "objections": [
                         {"check": "structural_analysis", "category": "capability", "request": "hard_block",
                          "summary": "Injected mistaken demand for missing physical inputs"},
                         {"check": "semantic_detail", "category": "preference", "request": "hard_block",
                          "summary": "Injected mistaken demand for resolved semantics"},
                     ], "summary": "Deliberate false veto control"}
        result = adjudicate(challenge, event["binding"], checks(candidate))
        self.assertEqual(result["outcome"], "retain_candidate_with_warning")
        self.assertEqual(result["false_objections"], 2)
        self.assertEqual(result["unsupported_hard_block_requests"], 2)

    def test_stale_criticism_never_triggers_repair_or_false_completion(self):
        for arm in ("A", "D"):
            report = self.trial(arm, "stale")
            self.assertEqual(report["status"], "stale_criticism")
            self.assertFalse(report["complete"])
            self.assertEqual(report["repairs"], [])
            self.assertFalse(any(call["kind"] == "repair" for call in report["calls"]))
            event = report["trajectory"][-1]
            self.assertEqual(event["decision"]["outcome"], "stale_criticism")
            self.assertEqual(event["decision"]["checks"], [])

    def test_feedback_binds_run_base_source_candidate_and_delta_exactly(self):
        report = self.trial("D")
        last_transition = None
        for event in report["trajectory"]:
            if event["outcome"] == "retained_candidate":
                last_transition = event
                continue
            if "binding" not in event:
                continue
            binding = event["binding"]
            self.assertEqual(binding, {"run": report["run"], "source": last_transition["source_ref"],
                                       "candidate": last_transition["candidate_ref"], "delta": last_transition["delta_ref"]})
            source = self.record(binding["source"])
            candidate = self.record(binding["candidate"])
            operator = StateRecordOperator.from_dict(self.artifact(binding["delta"]))
            self.assertEqual(operator.base_record_digest, source.digest)
            self.assertEqual(operator.base_state_digest, source.state_digest)
            self.assertEqual(source.run_ref.to_dict(), binding["run"])
            self.assertEqual(candidate.run_ref.to_dict(), binding["run"])
            challenge = self.artifact(event["challenge_ref"])["answer"]
            self.assertEqual(challenge["binding"], binding)
            observed = checks(candidate, final=event["checkpoint"] == 3)
            self.assertEqual(adjudicate(challenge, binding, observed), event["decision"])
            for path in (("run", "run_id"), ("run", "base", "state_sha256"),
                         ("run", "base", "version"), ("source", "sha256"),
                         ("candidate", "sha256"), ("delta", "sha256")):
                altered = deepcopy(challenge)
                target = altered["binding"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "different"
                decision = adjudicate(altered, binding, observed)
                self.assertEqual(decision["outcome"], "stale_criticism")
                self.assertEqual(decision["checks"], [])

    def test_timeout_missing_omission_and_missed_error_remain_in_denominators(self):
        rows = [self.trial("A", scenario) for scenario in ("wrong_entry", "timeout", "missing", "omission", "missed")]
        self.assertEqual([row["status"] for row in rows],
                         ["complete", "timeout", "malformed", "round_exhausted", "incomplete"])
        stats = summarize(rows)["A"]
        self.assertEqual(stats["trials"], 5)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["completion_rate"], 0.2)
        self.assertEqual(stats["propagation"]["introduced"], 4)
        self.assertEqual(stats["propagation"]["detected"], 1)
        self.assertEqual(stats["propagation"]["censored"], 2)
        self.assertEqual(stats["propagation"]["missed"], 1)
        self.assertEqual(stats["propagation"]["detected_depth"]["values"], [3])
        for report in (rows[1], rows[2], rows[4]):
            self.assertFalse(report["complete"])
            self.assertIsNone(report["propagation"][0]["depth"])
        self.assertEqual(rows[4]["propagation"][0]["observed_depth_lower_bound"], 3)
        self.assertEqual(stats["metrics"]["api_equivalent_cost_usd"]["missing"], 2)
        self.assertIn("gallery", rows[3]["final_assessment"]["unresolved"])
        self.assertIn("unit_access", rows[3]["final_assessment"]["unresolved"])

    def test_timeout_after_dependencies_accrue_is_censored_with_nonzero_lower_bound(self):
        report = self.trial(provider=ObservedProvider(fail_at=4))
        self.assertEqual(report["status"], "timeout")
        self.assertEqual(report["propagation"][0]["status"], "censored")
        self.assertIsNone(report["propagation"][0]["depth"])
        self.assertEqual(report["propagation"][0]["observed_depth_lower_bound"], 3)
        self.assertFalse(report["complete"])

    def test_call_budget_includes_review_and_repair_and_cannot_complete_without_review(self):
        for arm, limit, expected_calls in (("A", 3, 3), ("B", 1, 1), ("D", 2, 2)):
            report = self.trial(arm, budget=Budget(calls=limit))
            self.assertEqual(report["status"], "budget_exhausted")
            self.assertEqual(len(report["calls"]), expected_calls)
            self.assertFalse(report["complete"])
        report = self.trial("A", "valid", budget=Budget(calls=3))
        self.assertTrue(report["final_assessment"]["complete"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["status"], "budget_exhausted")

    def test_repair_round_ceiling_keeps_detected_but_unfixed_error(self):
        report = self.trial("A", "veto_loop")
        self.assertEqual(report["status"], "round_exhausted")
        self.assertFalse(report["complete"])
        self.assertEqual(sum(call["kind"] == "repair" for call in report["calls"]), 1)
        self.assertEqual(report["propagation"][0]["status"], "detected")
        self.assertEqual(report["propagation"][0]["depth"], 3)
        self.assertNotIn("resolved_step", report["propagation"][0])

    def test_reported_cost_overshoot_and_missing_cost_cannot_complete(self):
        provider = ObservedProvider(cost=2)
        report = self.trial(provider=provider, budget=Budget(api_equivalent_usd=1))
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(report["metrics"]["api_equivalent_cost_usd"], 2)
        self.assertEqual(len(report["calls"]), 1)
        self.assertEqual(report["trajectory"], [])
        self.assertFalse(report["complete"])
        # A provider represented as real requires measured cost; never infer zero.
        provider = ObservedProvider(missing_cost=True)
        with patch.object(provider, "describe", return_value={"kind": "real", "model": "test-double"}):
            report = self.trial(provider=provider)
        self.assertEqual(report["status"], "telemetry_missing")
        self.assertIsNone(report["metrics"]["api_equivalent_cost_usd"])
        self.assertFalse(report["complete"])

    def test_global_wall_budget_records_late_success_as_timeout_without_applying_it(self):
        clock = {"now": 0.0}

        class SlowProvider(ObservedProvider):
            def call(self, *args, **kwargs):
                result = super().call(*args, **kwargs)
                clock["now"] = 2.0
                return result

        provider = SlowProvider()
        with patch("labs.checkpoint_critique.harness.perf_counter", side_effect=lambda: clock["now"]):
            report = self.trial(provider=provider, budget=Budget(seconds=1, call_seconds=5))
        self.assertEqual(provider.options[0]["timeout_seconds"], 1)
        self.assertEqual(report["status"], "timeout")
        self.assertFalse(report["complete"])
        self.assertEqual(report["final_ref"], report["initial_ref"])
        self.assertEqual(len(report["calls"]), 1)

    def test_provider_exception_is_retained_as_failure_without_private_transport_text(self):
        report = self.trial(provider=ObservedProvider(raise_at=2))
        self.assertEqual(report["status"], "provider_error")
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["calls"]), 2)
        self.assertEqual(report["propagation"][0]["status"], "censored")
        self.assertNotIn("private transport text", json.dumps(report))

    def test_independent_final_judgment_rejects_critic_omission(self):
        provider = ObservedProvider("missed")
        report = self.trial("C", provider=provider)
        review = next(call for call in report["calls"] if call["kind"] == "review")
        self.assertEqual(review["answer"]["objections"], [])
        self.assertEqual(review["answer"]["requested_checks"], [])
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["final_assessment"]["unresolved"], ["entry_alignment"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["metrics"]["missed_errors"], 1)
        self.assertTrue(all("final_assessment" not in prompt for prompt in provider.prompts))

    def test_cold_replay_verifies_success_and_all_failure_outcomes(self):
        rows = [self.trial("A", scenario) for scenario in ("wrong_entry", "invariant", "timeout", "missing",
                                                           "omission", "stale", "missed", "veto_loop")]
        cold = FilesystemProjectRepository.open(self.repository.layout.root)
        for report in rows:
            result = replay(cold, report["report_ref"])
            self.assertEqual(result["status"], report["status"])
            self.assertEqual(result["calls_verified"], len(report["calls"]))
            self.assertEqual(result["candidate_digest"], self.record(report["final_ref"]).digest)
            self.assertEqual(result["final_assessment"], report["final_assessment"])

    def test_cold_replay_rejects_modified_artifact_bytes(self):
        report = self.trial()
        reference = report["calls"][0]["response_ref"]
        path = self.repository.layout.root / reference["relative_path"]
        path.write_bytes(b"{}")
        with self.assertRaises(ProjectIntegrityError):
            replay(FilesystemProjectRepository.open(self.repository.layout.root), report["report_ref"])

    def test_cold_replay_rejects_mismatched_source_even_with_valid_artifact_hash(self):
        report = self.trial()
        altered = deepcopy(report)
        altered["trajectory"][0]["source_ref"] = report["final_ref"]
        with self.assertRaises(ValueError):
            replay(self.repository, self.rewritten_report(altered))

    def test_cold_replay_rejects_mismatched_review_binding_and_call_claim(self):
        report = self.trial()
        altered = deepcopy(report)
        event = next(event for event in altered["trajectory"] if "binding" in event)
        event["binding"]["source"] = report["initial_ref"]
        with self.assertRaises(ValueError):
            replay(self.repository, self.rewritten_report(altered))
        altered = deepcopy(report)
        altered["calls"][0]["answer"]["action"]["entry_side"] = "west"
        with self.assertRaises(ValueError):
            replay(self.repository, self.rewritten_report(altered))

    def test_same_design_bound_to_distinct_runs_does_not_invent_diversity(self):
        rows = [self.trial("A", "valid") for _ in range(2)]
        self.assertNotEqual(rows[0]["final_ref"], rows[1]["final_ref"])
        self.assertEqual(self.record(rows[0]["final_ref"]).digest, self.record(rows[1]["final_ref"]).digest)
        self.assertEqual(summarize(rows)["A"]["successful_candidate_diversity"], 1)

    def test_batch_counterbalances_order_retains_all_scheduled_failures_and_replays(self):
        with patch("builtins.print"):
            result = run_batch(self.repository, "bounded-test", real=False, repeats=2,
                               parallel_blocks=1, scenario="timeout")
        self.assertEqual(result["replayed_trials"], 8)
        plan = self.artifact(result["plan_ref"])
        self.assertEqual(plan["schedule"], [["A", "B", "C", "D"], ["B", "C", "D", "A"]])
        self.assertEqual(plan["kind"], "deterministic_control")
        self.assertEqual(len(result["trials"]), 8)
        for group in result["summary"].values():
            self.assertEqual(group["trials"], 2)
            self.assertEqual(group["completed"], 0)
            self.assertEqual(group["completion_rate"], 0)
            self.assertEqual(group["outcomes"], {"timeout": 2})
            self.assertEqual(group["propagation"]["censored"], 2)
            self.assertEqual(group["propagation"]["detected_depth"]["n"], 0)
            self.assertIsNone(group["propagation"]["detected_depth"]["mean"])

    def test_batch_harness_exception_keeps_scheduled_arm_and_continues_without_retry(self):
        attempted = []

        def failing_trial(repository, run_id, arm, provider, budget, *, repetition):
            attempted.append(arm)
            if arm == "B":
                # Fail after a run exists, as a parser/persistence problem could.
                repository.create_run(run_id)
                raise RuntimeError("private internal error must not be retained")
            return run_trial(repository, run_id, arm, provider, budget, repetition=repetition)

        with patch("labs.checkpoint_critique.benchmark.run_trial", side_effect=failing_trial), patch("builtins.print"):
            result = run_batch(self.repository, "exception-batch", real=False, repeats=1, scenario="valid")
        self.assertEqual(attempted, ["A", "B", "C", "D"])
        self.assertEqual(len(result["trials"]), 4)
        self.assertEqual(result["replayed_trials"], 3)
        failed = result["summary"]["B"]
        self.assertEqual(failed["trials"], 1)
        self.assertEqual(failed["completed"], 0)
        self.assertEqual(failed["outcomes"], {"harness_error": 1})
        self.assertEqual(failed["metrics"]["api_equivalent_cost_usd"]["missing"], 1)
        self.assertIsNone(failed["metrics"]["api_equivalent_cost_usd"]["mean"])
        self.assertEqual(failed["propagation"]["unavailable_trials"], 1)
        self.assertNotIn("private internal error", json.dumps(self.artifact(result["trials"][1])))


if __name__ == "__main__":
    unittest.main()
