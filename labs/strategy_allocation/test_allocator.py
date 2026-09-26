"""AllocationState -> NextAllocation adapters over the existing SequentialAllocator."""

import unittest
from dataclasses import fields

from .allocator import AllocationState, SequentialBaseline, StrategyStatistics, make_rule

ARMS = ("A", "B", "C", "D")


def state(outcomes, **kwargs):
    kwargs.setdefault("remaining_budget", 20)
    kwargs.setdefault("attempts_so_far", sum(len(values) for values in outcomes.values()))
    return AllocationState(tuple(StrategyStatistics.from_outcomes(arm, outcomes.get(arm, [])) for arm in ARMS), **kwargs)


class AllocatorTests(unittest.TestCase):
    def test_statistics_count_every_attempt_including_failures(self):
        stats = StrategyStatistics.from_outcomes("A", [1.0, 0.0, 0.0])
        self.assertEqual((stats.n, stats.sample_mean), (3, 1 / 3))
        self.assertAlmostEqual(stats.sample_variance, 1 / 3)
        self.assertEqual(StrategyStatistics.from_outcomes("B", [1.0]), StrategyStatistics("B", 1, 1.0, None))

    def test_initialization_is_the_rules_own_balanced_warmup(self):
        for policy in ("equal", "round_robin"):
            with self.subTest(policy=policy):
                decision = make_rule(policy).next_allocation(state({}, parallel_capacity=8, warmup=2))
                self.assertEqual(decision.as_dict(), {"A": 2, "B": 2, "C": 2, "D": 2})
                self.assertIsNone(decision.stopping_reason)
                self.assertTrue(all("warmup" in note for note in decision.diagnostics[1:]))
                self.assertTrue(decision.diagnostics[0].startswith("sequential-ocba-v2/"))

    def test_equal_gives_the_next_rollout_to_the_least_sampled_strategy(self):
        outcomes = {"A": [1, 1, 0], "B": [0, 1], "C": [1, 1, 1], "D": [0, 0, 0]}
        self.assertEqual(make_rule("equal").next_allocation(state(outcomes)).as_dict(), {"B": 1})

    def test_round_robin_rotates_by_attempt_step(self):
        warm = {arm: [1, 0] for arm in ARMS}
        rule = make_rule("round_robin")
        chosen = [next(iter(rule.next_allocation(state(warm, attempts_so_far=step)).as_dict())) for step in range(8, 13)]
        self.assertEqual(chosen, ["A", "B", "C", "D", "A"])

    def test_a_batch_spreads_like_consecutive_sequential_decisions(self):
        warm = {arm: [1, 0] for arm in ARMS}
        decision = make_rule("equal").next_allocation(state(warm, parallel_capacity=6))
        self.assertEqual(decision.allocations, (("A", 2), ("B", 2), ("C", 1), ("D", 1)))

    def test_the_rule_stops_when_the_budget_is_spent(self):
        warm = {arm: [1, 0] for arm in ARMS}
        spent = make_rule("equal").next_allocation(state(warm, remaining_budget=0))
        self.assertEqual((spent.allocations, spent.stopping_reason), ((), "insufficient_budget"))
        partial = make_rule("equal").next_allocation(state(warm, remaining_budget=3, parallel_capacity=4))
        self.assertEqual((partial.total, partial.stopping_reason), (3, None))

    def test_baselines_read_counts_not_outcome_values(self):
        high = {arm: [1, 1] for arm in ARMS}
        low = {arm: [0, 1] for arm in ARMS}
        for policy in ("equal", "round_robin"):
            rule = make_rule(policy)
            self.assertEqual(rule.next_allocation(state(high, parallel_capacity=4)),
                             rule.next_allocation(state(low, parallel_capacity=4)))

    def test_the_interface_carries_statistics_and_budget_only(self):
        names = {item.name for item in fields(AllocationState)} | {item.name for item in fields(StrategyStatistics)}
        self.assertFalse(any(word in name for name in names for word in ("token", "transcript", "proposal", "plan")))

    def test_ocba_reuses_the_existing_rule_and_observes_between_allocations(self):
        outcomes = {
            "A": [1, 0, 1, 0],
            "B": [1, 1, 1, 0],
            "C": [1, 0, 0, 0],
            "D": [1, 1, 0, 0],
        }
        decision = make_rule("ocba").next_allocation(state(outcomes, parallel_capacity=8))
        self.assertEqual(decision.total, 1)
        self.assertIn(next(iter(decision.as_dict())), ARMS)
        self.assertEqual(decision.diagnostics[0], "sequential-ocba-v2/ocba")
        self.assertIn("classical OCBA", decision.diagnostics[1])
        self.assertIn("one rollout", decision.diagnostics[2])

    def test_ocba_balances_when_bernoulli_warmup_has_zero_variance(self):
        outcomes = {"A": [1, 1], "B": [1, 0], "C": [0, 0], "D": [1, 0]}
        decision = make_rule("ocba").next_allocation(state(outcomes))
        self.assertEqual(decision.as_dict(), {"A": 1})
        self.assertIn("zero empirical variance", decision.diagnostics[1])

    def test_only_supported_rules_exist_in_this_slice(self):
        for name in ("cost_ocba", "epsilon_greedy"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                make_rule(name)
        with self.assertRaises(ValueError):
            SequentialBaseline("ocba")

    def test_invalid_states_are_refused(self):
        stats = StrategyStatistics.from_outcomes("A", [])
        for kwargs in ({"strategies": (stats, stats), "remaining_budget": 1}, {"strategies": (stats,), "remaining_budget": -1},
                       {"strategies": (stats,), "remaining_budget": 1, "parallel_capacity": 0}, {"strategies": (), "remaining_budget": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AllocationState(**kwargs)


if __name__ == "__main__":
    unittest.main()
