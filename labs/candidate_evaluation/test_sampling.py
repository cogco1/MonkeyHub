"""Observable budget/statistics/replay tests for the controlled experiment."""

from dataclasses import replace
from math import isclose
import unittest

from .allocation_benchmark import aggregate, run_trial, stream_seed, synthetic_fixtures, wilson
from .benchmark import ENVELOPE, candidate, request_for
from .evaluator import ConstraintResult, MassingEvaluator, summarize
from .sampling import ControlledSampler, NoisePolicy, estimate_from_evaluation


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.source = MassingEvaluator(ENVELOPE).evaluate(request_for(candidate("sample-test")))

    def test_gaussian_statistics_use_existing_summary(self):
        sampler = ControlledSampler(self.source, NoisePolicy(10., 2.), 1)
        observations = [sampler.sample().value for _ in range(1000)]
        self.assertEqual(sampler.statistics, summarize(observations))
        self.assertAlmostEqual(sampler.statistics.mean, 10., delta=.2)
        self.assertAlmostEqual(sampler.statistics.variance, 4., delta=.5)
        result = sampler.evaluation()
        self.assertEqual(result.candidate_digest, self.source.candidate_digest)
        self.assertEqual(result.constraints, self.source.constraints)
        estimate = estimate_from_evaluation(result, per_sample_cost=3.)
        self.assertEqual(estimate.statistics, sampler.statistics)

    def test_failed_attempts_retain_cost_but_not_numeric_scores(self):
        sampler = ControlledSampler(self.source, NoisePolicy(1., .5, 10., .8), 5)
        observations = [sampler.sample() for _ in range(20)]
        successes = [item.value for item in observations if item.failure is None]
        self.assertTrue(0 < len(successes) < 20)
        self.assertEqual(sampler.statistics, summarize(successes))
        self.assertEqual(sampler.cost, 200.)
        self.assertEqual(sampler.attempts, 20)
        self.assertEqual([item.index for item in observations], list(range(1, 21)))
        self.assertTrue(all(item.value is None for item in observations if item.failure))

    def test_replay_is_a_separate_identical_stream_not_appended_evidence(self):
        first = ControlledSampler(self.source, NoisePolicy(1., 1.), 42)
        replay = ControlledSampler(self.source, NoisePolicy(1., 1.), 42)
        self.assertEqual([first.sample() for _ in range(12)], [replay.sample() for _ in range(12)])
        self.assertEqual(first.statistics.count, 12)
        self.assertNotEqual(stream_seed(5, "a", 0, 0), stream_seed(5, "a", 1, 0))
        self.assertNotEqual(stream_seed(5, "a", 0, 0), stream_seed(5, "a", 0, 1))

    def test_invalid_and_deterministic_sampling_boundaries(self):
        invalid = replace(self.source, constraints=(ConstraintResult("hard", "fail", "test"),))
        sampler = ControlledSampler(invalid, NoisePolicy(1000., 1.), 2)
        with self.assertRaisesRegex(ValueError, "invalid or unavailable"):
            sampler.sample()
        self.assertEqual(sampler.cost, 0.)
        deterministic = ControlledSampler(self.source, NoisePolicy(2., 0.), 2)
        self.assertEqual(deterministic.sample().value, 2.)
        with self.assertRaisesRegex(ValueError, "resampled"):
            deterministic.sample()
        self.assertEqual(deterministic.statistics.variance, 0.)

    def test_nonfinite_policy_refused(self):
        for kwargs in ({"mean": float("nan"), "deviation": 1.},
                       {"mean": 1., "deviation": -1.}, {"mean": 1., "deviation": 1., "cost": 0.},
                       {"mean": 1., "deviation": 1e200}, {"mean": True, "deviation": 1.}):
            with self.assertRaises(ValueError):
                NoisePolicy(**kwargs)


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = {f.name: f for f in synthetic_fixtures()}

    def trial(self, name="heterogeneous_cost", **kwargs):
        defaults = dict(policy="equal", budget=180, budget_unit="cost", repetition=0, master_seed=12)
        return run_trial(self.fixtures[name], **(defaults | kwargs))

    def test_warmup_and_failed_costs_are_in_total_budget(self):
        for policy in ("equal", "round_robin", "ocba", "cost_ocba"):
            row = self.trial(policy=policy)
            self.assertLessEqual(row["normalized_compute_cost"], 180)
            self.assertTrue(isclose(row["normalized_compute_cost"] + row["remaining_budget"], 180))
            self.assertEqual(row["attempts"], len(row["trace"]))
            self.assertEqual(row["attempts"], sum(row["attempts_by_candidate"]))
        missing = self.trial("missing_samples", budget=100, budget_unit="samples")
        self.assertGreater(missing["failures"], 0)
        self.assertEqual(missing["attempts"], missing["failures"] + missing["successful_samples"])
        self.assertEqual(missing["attempts_by_candidate"][-1], 0)

    def test_trace_replay_and_final_statistics(self):
        first = self.trial("close_top_two", policy="ocba", budget=100, budget_unit="samples")
        second = self.trial("close_top_two", policy="ocba", budget=100, budget_unit="samples")
        self.assertEqual(first | {"elapsed_seconds": 0}, second | {"elapsed_seconds": 0})
        for i, estimate in enumerate(first["final_estimates"]):
            values = [event[2] for event in first["trace"] if event[0] == i and event[3] is None]
            self.assertEqual(estimate["statistics"], vars(summarize(values)))

    def test_insufficient_initial_budget_does_not_claim_full_set_selection(self):
        row = self.trial("easy", budget=3, budget_unit="samples")
        self.assertIsNone(row["selected_candidate"])
        self.assertIsNone(row["simple_regret"])
        self.assertFalse(row["correct_selection"])
        self.assertFalse(row["warmup_complete"])

    def test_mixed_deterministic_not_resampled(self):
        row = self.trial("deterministic_mixture", policy="ocba", budget=100, budget_unit="samples")
        for i in (0, 2, 4):
            self.assertEqual(row["attempts_by_candidate"][i], 1)

    def test_aggregate_counts_unselected_and_reports_uncertainty(self):
        a = self.trial("easy", budget=3, budget_unit="samples")
        b = self.trial("easy", budget=3, budget_unit="samples", repetition=1)
        summary = aggregate([a, b])
        self.assertEqual(summary["unselected_trials"], 2)
        self.assertIsNone(summary["mean_simple_regret"])
        self.assertEqual(summary["pcs"], 0.)
        self.assertGreater(wilson(0, 2)[1], 0.)


if __name__ == "__main__":
    unittest.main()
