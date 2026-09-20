"""Observable budget/validity boundaries and hand-derived allocation checks."""

from dataclasses import replace
from math import sqrt
import unittest

from .allocation import CandidateEstimate, EvaluationBudgetRequest, SequentialAllocator
from .evaluator import SampleStatistics, summarize


def estimate(name, mean=0.0, variance=1.0, count=5, cost=1.0, **kwargs):
    stats = SampleStatistics(count, mean, variance, sqrt(variance / count))
    return CandidateEstimate(name, stats, cost, **kwargs)


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.allocator = SequentialAllocator()

    def allocate(self, estimates, **kwargs):
        kwargs.setdefault("remaining_budget", 10)
        return self.allocator.allocate(EvaluationBudgetRequest(tuple(estimates), **kwargs))

    def test_invalid_and_unavailable_never_win_or_receive_samples(self):
        candidates = (estimate("bad", 1e9, validity="invalid"),
                      estimate("unknown", 1e9, validity="unavailable"), estimate("good", 0))
        for policy in ("equal", "round_robin", "variance", "epsilon_greedy", "ocba"):
            with self.subTest(policy=policy):
                decision = self.allocate(candidates, policy=policy)
                self.assertEqual(decision.candidate_id, "good")
        empty = self.allocate(candidates[:2])
        self.assertIsNone(empty.candidate_id)
        self.assertEqual(empty.stop_reason, "no_eligible_candidates")

    def test_missing_statistics_request_paid_warmup(self):
        unknown = CandidateEstimate("unknown", summarize(()), 3)
        decision = self.allocate((unknown,), budget_unit="cost", remaining_budget=3, policy="ocba")
        self.assertEqual(decision.candidate_id, "unknown")
        self.assertIn("warmup", decision.reason)
        self.assertEqual(decision.details["unit_cost"], 3)
        self.assertEqual(unknown.statistics.count, 0)
        self.assertIsNone(self.allocate((unknown,), budget_unit="cost", remaining_budget=2).candidate_id)

    def test_warmup_one_does_not_pretend_single_stochastic_sample_has_variance(self):
        item = CandidateEstimate("one", summarize((3,)), 1)
        decision = self.allocate((item,), policy="ocba", warmup=1)
        self.assertIn("warmup", decision.reason)
        self.assertEqual(decision.candidate_id, "one")

    def test_known_deterministic_is_not_sampled_again(self):
        known = estimate("known", 100, variance=0, count=1, deterministic=True)
        noisy = estimate("noisy", 0)
        for policy in ("equal", "round_robin", "variance", "epsilon_greedy", "ocba"):
            with self.subTest(policy=policy):
                self.assertEqual(self.allocate((known, noisy), policy=policy).candidate_id, "noisy")
        self.assertEqual(self.allocate((known,)).stop_reason, "all_deterministic_known")
        unknown = replace(known, statistics=summarize(()))
        self.assertEqual(self.allocate((unknown,)).candidate_id, "known")

    def test_equal_balances_successful_observations(self):
        decision = self.allocate((estimate("a", count=10), estimate("b", count=5)))
        self.assertEqual(decision.candidate_id, "b")

    def test_round_robin_rotates_and_skips_unaffordable(self):
        candidates = (estimate("c"), estimate("a", cost=10), estimate("b"))
        chosen = [self.allocate(candidates, policy="round_robin", step=step,
                                budget_unit="cost", remaining_budget=3).candidate_id for step in range(3)]
        self.assertEqual(chosen, ["b", "c", "b"])

    def test_round_robin_excludes_known_deterministic_without_duplicate_turns(self):
        candidates = (estimate("a", variance=0, count=1, deterministic=True),
                      estimate("b"), estimate("c"), estimate("d"))
        chosen = [self.allocate(candidates, policy="round_robin", step=step).candidate_id for step in range(6)]
        self.assertEqual(chosen, ["b", "c", "d", "b", "c", "d"])

    def test_variance_baseline_measures_uncertainty_of_mean(self):
        decision = self.allocate((estimate("a", variance=9, count=10), estimate("b", variance=5, count=5)),
                                 policy="variance")
        self.assertEqual(decision.candidate_id, "b")

    def test_epsilon_stream_is_replayable_without_sampling_or_mutation(self):
        candidates = (estimate("a", mean=10), estimate("b", mean=1))
        left = [self.allocate(candidates, policy="epsilon_greedy", step=step, seed=3) for step in range(30)]
        right = [self.allocate(candidates, policy="epsilon_greedy", step=step, seed=3) for step in range(30)]
        self.assertEqual(left, right)
        self.assertEqual([item.statistics.count for item in candidates], [5, 5])
        self.assertEqual(set(item.candidate_id for item in left), {"a", "b"})

    def test_zero_or_insufficient_budget_never_selects(self):
        item = estimate("a", cost=2)
        self.assertEqual(self.allocate((item,), remaining_budget=0).stop_reason, "insufficient_budget")
        self.assertIsNone(self.allocate((item,), budget_unit="cost", remaining_budget=1.9).candidate_id)
        self.assertEqual(self.allocate((item,), budget_unit="cost", remaining_budget=2).candidate_id, "a")

    def test_classical_ratios_match_hand_calculation(self):
        # B: 9/(10-8)^2 = 9/4; C: 4/(10-6)^2 = 1/4.
        # A: sqrt(4) * sqrt((9/4)^2/9 + (1/4)^2/4).
        candidates = (estimate("a", 10, 4), estimate("b", 8, 9), estimate("c", 6, 4))
        decision = self.allocate(candidates, policy="ocba", remaining_budget=100)
        ratios = {"a": 2 * sqrt((9 / 4) ** 2 / 9 + (1 / 4) ** 2 / 4), "b": 9 / 4, "c": 1 / 4}
        for name, ratio in ratios.items():
            self.assertAlmostEqual(decision.details[f"share:{name}"], ratio / sum(ratios.values()))
            self.assertAlmostEqual(decision.details[f"target:{name}"], 115 * ratio / sum(ratios.values()))
        self.assertEqual(decision.candidate_id, "b")

    def test_cost_formula_keeps_nonbest_sample_ratios_and_changes_best_balance(self):
        candidates = (estimate("a", 10, 4, cost=4), estimate("b", 8, 9, cost=1), estimate("c", 6, 4, cost=2))
        decision = self.allocate(candidates, policy="cost_ocba", budget_unit="cost", remaining_budget=100)
        ratios = {"a": 2 * sqrt((1 / 4) * (9 / 4) ** 2 / 9 + (2 / 4) * (1 / 4) ** 2 / 4),
                  "b": 9 / 4, "c": 1 / 4}
        cost_sum = 4 * ratios["a"] + ratios["b"] + 2 * ratios["c"]
        for name, ratio in ratios.items():
            self.assertAlmostEqual(decision.details[f"target:{name}"], 135 * ratio / cost_sum)
        self.assertAlmostEqual(decision.details["share:b"] / decision.details["share:c"], 9)
        self.assertAlmostEqual(sum(item.per_sample_cost * decision.details[f"target:{item.candidate_id}"]
                                   for item in candidates), 135)

    def test_equal_cost_ocba_reduces_to_classic_target(self):
        candidates = (estimate("a", 10, 4), estimate("b", 8, 9), estimate("c", 6, 4))
        classic = self.allocate(candidates, policy="ocba", remaining_budget=100)
        cost = self.allocate(candidates, policy="cost_ocba", budget_unit="cost", remaining_budget=100)
        self.assertEqual(classic.details, cost.details)
        self.assertEqual(classic.candidate_id, cost.candidate_id)

    def test_two_arm_cost_formula_reduces_to_neyman_cost_ratio(self):
        # s=(1,2), c=(4,1): N_a/N_b=(s_a/s_b)*sqrt(c_b/c_a)=1/4.
        candidates = (estimate("a", 10, 1, cost=4), estimate("b", 8, 4, cost=1))
        decision = self.allocate(candidates, policy="cost_ocba", budget_unit="cost", remaining_budget=100)
        self.assertAlmostEqual(decision.details["target:a"] / decision.details["target:b"], 0.25)
        self.assertAlmostEqual(4 * decision.details["target:a"] + decision.details["target:b"], 125)

    def test_existing_observations_are_retained_not_refunded(self):
        candidates = (estimate("a", 10, 4, count=100), estimate("b", 8, 9), estimate("c", 6, 4))
        decision = self.allocate(candidates, policy="ocba", remaining_budget=100)
        self.assertEqual(decision.details["target:a"], 100)
        self.assertAlmostEqual(decision.details["target:b"], 99)
        self.assertAlmostEqual(decision.details["target:c"], 11)
        self.assertEqual(decision.candidate_id, "b")

    def test_ties_and_zero_empirical_variance_trigger_explicit_extra_sampling(self):
        cases = ((estimate("a", 10), estimate("b", 10)),
                 (estimate("a", 10, variance=0), estimate("b", 8)),
                 (estimate("a", 10, variance=0), estimate("b", 8, variance=0)))
        for candidates in cases:
            with self.subTest(candidates=candidates):
                decision = self.allocate(candidates, policy="ocba")
                self.assertEqual(decision.candidate_id, "a")
                self.assertIn("explicit heuristic", decision.reason)
                self.assertIsNone(decision.stop_reason)

    def test_near_ties_do_not_overflow_or_need_an_arbitrary_gap_floor(self):
        candidates = (estimate("a", 0), estimate("b", -1e-200), estimate("c", -1))
        decision = self.allocate(candidates, policy="ocba", remaining_budget=100)
        self.assertIn(decision.candidate_id, {"a", "b"})
        self.assertAlmostEqual(decision.details["share:a"], 0.5)
        self.assertAlmostEqual(decision.details["share:b"], 0.5)
        self.assertEqual(decision.details["target:c"], 5)

    def test_extreme_finite_mean_difference_uses_finite_ratios(self):
        candidates = (estimate("a", 1e308), estimate("b", -1e308), estimate("c", -9e307))
        decision = self.allocate(candidates, policy="ocba", remaining_budget=100)
        self.assertAlmostEqual(sum(decision.details[f"share:{item.candidate_id}"] for item in candidates), 1)

    def test_mixed_deterministic_comparison_uses_labeled_sampling_fallback(self):
        candidates = (estimate("known", 9, variance=0, count=1, deterministic=True),
                      estimate("a", 10, count=7), estimate("b", 8, count=5))
        decision = self.allocate(candidates, policy="ocba")
        self.assertEqual(decision.candidate_id, "b")
        self.assertIn("known deterministic mixture", decision.reason)
        self.assertIn("heuristic", decision.reason)

    def test_request_validation_and_duplicate_identity(self):
        item = estimate("a")
        for change in ({"remaining_budget": -1}, {"remaining_budget": float("nan")},
                       {"remaining_budget": float("inf")}, {"remaining_budget": 0.5},
                       {"remaining_budget": True}, {"budget_unit": "minutes"}, {"policy": "magic"},
                       {"policy": "cost_ocba"}, {"warmup": 0}, {"warmup": True}, {"step": -1},
                       {"seed": 0.5}, {"estimates": (item, item)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                kwargs = {"estimates": (item,), "remaining_budget": 10} | change
                EvaluationBudgetRequest(**kwargs)
        with self.assertRaises(TypeError):
            EvaluationBudgetRequest((item,))

    def test_statistics_validation(self):
        for stats in (SampleStatistics(-1, None, None, None), SampleStatistics(True, 1, None, None),
                      SampleStatistics(0, 1, None, None), SampleStatistics(1, None, None, None),
                      SampleStatistics(1, 1, 0, 0), SampleStatistics(2, 1, None, None),
                      SampleStatistics(2, float("nan"), 1, sqrt(0.5)), SampleStatistics(2, 1, -1, 0),
                      SampleStatistics(2, 1, float("inf"), 0), SampleStatistics(2, 1, 1, -1),
                      SampleStatistics(2, 1, 1, 1)):
            with self.subTest(stats=stats), self.assertRaises(ValueError):
                CandidateEstimate("bad", stats, 1)
        for change in ({"per_sample_cost": 0}, {"per_sample_cost": float("inf")},
                       {"per_sample_cost": True}, {"candidate_id": " "}, {"validity": "unchecked"},
                       {"deterministic": 1}, {"deterministic": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(estimate("a"), **change)


if __name__ == "__main__":
    unittest.main()
