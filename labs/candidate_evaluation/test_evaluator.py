"""Hand-calculated massing, failure and stochastic-reference checks for GH-123."""

from dataclasses import replace
import json
from math import sqrt
from statistics import fmean
import unittest

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.state_record import Entity, StateRecord
from .benchmark import (
    CONTEXT, ENVELOPE, EVIDENCE, candidate, multiobjective_candidates, request_for,
    run_benchmark, validity_cases,
)
from .evaluator import (
    EvaluationRequest, MassingEvaluator, Objective, dominates, evaluate_timed,
    normalize, pareto_front, summarize,
)
from .synthetic import SyntheticEvaluator


class MassingTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = MassingEvaluator(ENVELOPE)
        self.request = request_for(candidate("test"))

    def test_hand_calculated_four_objectives(self):
        # 4x3 plate, two 3m storeys: 12m2 footprint, 24m2 GFA, 2 floors, 6m height.
        result = self.evaluator.evaluate(self.request)
        self.assertEqual(result.validity, "valid")
        self.assertEqual([item.value for item in result.objectives], [12, 24, 2, 6])
        self.assertEqual([(item.objective.unit, item.objective.direction) for item in result.objectives],
                         [("m2", "minimize"), ("m2", "maximize"), ("count", "maximize"), ("m", "minimize")])
        self.assertTrue(all(item.statistics.count == 1 and item.statistics.variance == 0
                            and item.statistics.standard_error == 0 for item in result.objectives))

    def test_identical_input_same_values_without_clock_in_domain_result(self):
        before = self.request.record.to_dict()
        first = evaluate_timed(self.evaluator, self.request)
        second = evaluate_timed(self.evaluator, self.request)
        self.assertEqual(first.result, second.result)
        self.assertEqual(self.request.record.to_dict(), before)
        self.assertGreater(first.elapsed_seconds, 0)
        self.assertEqual(first.seconds_per_sample, first.elapsed_seconds)
        self.assertIsNone(first.monetary_cost)

    def test_overlap_is_union_not_sum(self):
        first = candidate("overlap", floors=1)
        second_volume = Entity("east", "Volume@1", {"min": [2, 0, 0], "max": [5, 2, 2],
                               "level_ids": ["floor-0"]}, basis_refs=(EVIDENCE,))
        record = replace(first, entities=(*first.entities, second_volume))
        result = self.evaluator.evaluate(request_for(record))
        # x=0..5, z=0..2: 18, not two 12m2 footprints.
        self.assertEqual([item.value for item in result.objectives], [18, 18, 1, 3])

    def test_translation_does_not_change_area_or_height(self):
        left = self.evaluator.evaluate(self.request)
        right = self.evaluator.evaluate(request_for(candidate("shift", x=4)))
        self.assertEqual(left.objectives, right.objectives)
        self.assertNotEqual(left.candidate_digest, right.candidate_digest)

    def test_each_known_violation_fails_only_its_own_rule(self):
        expected = {"site-violation": "site_bounds", "height-violation": "height_limit",
                    "far-violation": "far_limit", "stale-base": "exact_binding"}
        for name, request, evaluator in validity_cases():
            if name not in expected:
                continue
            with self.subTest(name=name):
                result = evaluator.evaluate(request)
                self.assertEqual(result.validity, "invalid")
                self.assertEqual([check.name for check in result.constraints if check.status == "fail"],
                                 [expected[name]])

    def test_missing_or_partial_projection_is_not_zero_or_valid(self):
        for name, request, evaluator in validity_cases():
            if name not in {"missing-massing", "off-lattice"}:
                continue
            with self.subTest(name=name):
                result = evaluator.evaluate(request)
                self.assertEqual(result.validity, "unavailable")
                self.assertTrue(all(item.value is None and item.unavailable_reason for item in result.objectives))
                self.assertTrue(all(item.statistics.count == 0 for item in result.objectives))

    def test_missing_constraint_input_keeps_known_objectives(self):
        result = MassingEvaluator({"max_height_m": 12}).evaluate(self.request)
        self.assertEqual(result.validity, "unavailable")
        self.assertEqual(result.objectives[1].value, 24)
        self.assertEqual({check.name for check in result.constraints if check.status == "unavailable"},
                         {"site_bounds", "far_limit"})

    def test_fail_remains_invalid_when_another_constraint_is_unavailable(self):
        result = MassingEvaluator({"max_height_m": 2}).evaluate(self.request)
        self.assertEqual(result.validity, "invalid")

    def test_malformed_envelope_is_unavailable(self):
        for envelope in ({"min": (0, 0), "max": (1, 1, 1)}, {"min": (2, 0, 0), "max": (1, 1, 1)},
                         {"far": 2, "site_area_m2": 0}, {"max_height_m": "tall"}):
            with self.subTest(envelope=envelope):
                result = MassingEvaluator(envelope).evaluate(self.request)
                self.assertEqual(result.validity, "unavailable")

    def test_missing_source_evidence_is_unavailable(self):
        record = StateRecord(self.request.record.project_id, "no-evidence", (), base=self.request.record.base)
        result = self.evaluator.evaluate(EvaluationRequest(record, record.run_ref, record.digest, CONTEXT))
        self.assertEqual(result.validity, "unavailable")
        self.assertTrue(all(item.value is None for item in result.objectives))

    def test_content_run_project_and_base_are_bound(self):
        wrong_project = ProjectVersionRef("other-project", 0, self.request.expected_run.base.state_sha256)
        other_requests = (
            replace(self.request, expected_content_digest="0" * 64),
            replace(self.request, expected_run=RunRef(self.request.record.project_id, "other-run", self.request.record.base)),
            replace(self.request, expected_run=RunRef(wrong_project.project_id, "test", wrong_project)),
            replace(self.request, record=candidate("test", width=5)),
        )
        for request in other_requests:
            with self.subTest(request=request.expected_run):
                result = self.evaluator.evaluate(request)
                self.assertEqual(result.validity, "invalid")
                self.assertTrue(all(item.value is None for item in result.objectives))

    def test_unbound_record_cannot_claim_exact_evaluation(self):
        for base in (None, ProjectVersionRef(self.request.record.project_id, 0)):
            for evaluator in (self.evaluator, SyntheticEvaluator(10, 2, 8, 0)):
                result = evaluator.evaluate(replace(self.request, record=replace(self.request.record, base=base)))
                self.assertEqual(result.validity, "unavailable")
                self.assertTrue(all(item.value is None for item in result.objectives))
                json.dumps(result.to_dict(), allow_nan=False)

    def test_binding_failure_retains_requested_and_observed_source(self):
        result = self.evaluator.evaluate(replace(self.request, expected_content_digest="f" * 64))
        self.assertEqual(result.candidate_digest, "f" * 64)
        self.assertEqual(result.observed_candidate_digest, self.request.record.digest)
        self.assertEqual(result.observed_run, self.request.record.run_ref)
        self.assertEqual(result.validity, "invalid")

    def test_nonfinite_coordinates_are_unavailable_even_for_objective_subset(self):
        good = self.request.record
        variants = []
        for coordinate in ("min", "max"):
            volume = good.entities[-1]
            for axis in range(3):
                bounds = list(volume.fields[coordinate])
                bounds[axis] = "NaN"
                variants.append(replace(good, entities=(*good.entities[:-1], replace(
                    volume, fields={**volume.fields, coordinate: bounds}))))
        for key in ("base_y", "height"):
            level = good.entities[1]
            variants.append(replace(good, entities=(good.entities[0], replace(level, fields={**level.fields, key: "NaN"}),
                                                   *good.entities[2:])))
        for record in variants:
            result = MassingEvaluator(ENVELOPE, objective_names=("footprint_m2",)).evaluate(request_for(record))
            self.assertEqual(result.validity, "unavailable")
            self.assertIsNone(result.objectives[0].value)

    def test_request_requires_digest_and_context(self):
        with self.assertRaises(ValueError):
            replace(self.request, expected_run=RunRef(self.request.record.project_id, "test",
                                                    ProjectVersionRef(self.request.record.project_id, 0)))
        with self.assertRaises(ValueError):
            replace(self.request, context_refs=())


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        evaluator = MassingEvaluator(ENVELOPE)
        self.results = tuple(evaluator.evaluate(request_for(record)) for record in multiobjective_candidates())

    def test_twelve_variants_known_pareto_front(self):
        self.assertEqual(len(self.results), 12)
        self.assertEqual({result.run.run_id for result in pareto_front(self.results)},
                         {f"w{width}-f{floors}-h3" for width in (2, 3, 4) for floors in (1, 2)})
        self.assertFalse(dominates(self.results[0], self.results[0]))
        self.assertTrue(dominates(self.results[0], self.results[1]))

    def test_monotonic_single_objective(self):
        evaluator = MassingEvaluator(ENVELOPE, objective_names=("gross_floor_area_m2",))
        results = [evaluator.evaluate(request_for(candidate(f"w{width}", width=width))) for width in (2, 3, 4)]
        self.assertEqual([item.objectives[0].value for item in results], [12, 18, 24])
        self.assertEqual(pareto_front(results), (results[2],))

    def test_invalid_unavailable_and_stochastic_are_not_ranked(self):
        for _, request, evaluator in validity_cases()[1:]:
            with self.assertRaises(ValueError):
                pareto_front((evaluator.evaluate(request),))
        result = SyntheticEvaluator(10, 2, 4, 0).evaluate(request_for(candidate("noise")))
        with self.assertRaises(ValueError):
            dominates(result, result)

    def test_other_base_policy_units_or_context_refused(self):
        result = self.results[0]
        altered = (
            replace(result, run=RunRef(result.run.project_id, result.run.run_id,
                                      ProjectVersionRef(result.run.project_id, 1, result.run.base.state_sha256))),
            replace(result, configuration="{}"), replace(result, context_refs=("different-context",)),
            replace(result, evaluator_version="different-version"),
            replace(result, objectives=(replace(result.objectives[0], objective=Objective("footprint_m2", "ft2", "minimize")),
                                        *result.objectives[1:])),
        )
        for other in altered:
            with self.assertRaises(ValueError):
                dominates(result, other)

    def test_normalization_uses_declared_fixed_bounds_and_directions(self):
        result = self.results[0]  # footprint 6, GFA 6, one floor, height 3
        bounds = {"footprint_m2": (0, 12), "gross_floor_area_m2": (0, 24), "floor_count": (0, 2), "height_m": (0, 6)}
        self.assertEqual(normalize(result, bounds), {"footprint_m2": .5, "gross_floor_area_m2": .25,
                                                   "floor_count": .5, "height_m": .5})
        self.assertEqual(normalize(result, {**bounds, "gross_floor_area_m2": (0, 3)})["gross_floor_area_m2"], 2)
        with self.assertRaises(ValueError):
            normalize(result, {**bounds, "height_m": (2, 2)})
        with self.assertRaises(ValueError):
            normalize(result, {})


class StatisticsTests(unittest.TestCase):
    def test_hand_calculated_unbiased_variance(self):
        stats = summarize((8, 10, 12))
        self.assertEqual((stats.count, stats.mean, stats.variance), (3, 10, 4))
        self.assertEqual(stats.standard_error, sqrt(4 / 3))

    def test_zero_and_one_sample_are_explicit(self):
        empty = summarize(())
        self.assertEqual(empty.count, 0)
        self.assertIsNone(empty.mean)
        single = summarize((4,))
        self.assertEqual(single.mean, 4)
        self.assertIsNone(single.variance)
        self.assertIsNone(single.standard_error)
        self.assertEqual(summarize((4,), deterministic=True).variance, 0)

    def test_nonfinite_or_false_deterministic_samples_refused(self):
        for values in ((float("nan"),), (float("inf"),)):
            with self.assertRaises(ValueError):
                summarize(values)
        with self.assertRaises(ValueError):
            summarize((1, 2), deterministic=True)

    def test_controlled_two_point_reference_over_eight_seeds(self):
        request = request_for(candidate("noise"))
        results = [evaluate_timed(SyntheticEvaluator(10, 2, 2048, seed), request) for seed in range(8)]
        means = [run.result.objectives[0].statistics.mean for run in results]
        variances = [run.result.objectives[0].statistics.variance for run in results]
        # Eight separate batches, total 16384 draws. These are correctness tolerances,
        # not a claim of architectural calibration or a stochastic ranking method.
        self.assertLess(abs(fmean(means) - 10), .1)
        self.assertLess(abs(fmean(variances) - 4), .1)
        for run in results:
            stats = run.result.objectives[0].statistics
            self.assertEqual(stats.count, 2048)
            self.assertAlmostEqual(stats.standard_error, sqrt(stats.variance / 2048))
            self.assertEqual(run.seconds_per_sample, run.elapsed_seconds / 2048)
            self.assertGreater(run.seconds_per_sample, 0)
        self.assertEqual(SyntheticEvaluator(10, 2, 4, 8).evaluate(request),
                         SyntheticEvaluator(10, 2, 4, 8).evaluate(request))

    def test_no_sample_is_not_zero_score_or_zero_cost(self):
        run = evaluate_timed(SyntheticEvaluator(10, 2, 0, 0), request_for(candidate("no-sample")))
        self.assertIsNone(run.result.objectives[0].value)
        self.assertEqual(run.result.objectives[0].unavailable_reason, "no samples requested")
        self.assertIsNone(run.seconds_per_sample)

    def test_unrepresentable_sample_moments_are_unavailable(self):
        result = SyntheticEvaluator(0, 1e308, 8, 0).evaluate(request_for(candidate("large-noise")))
        self.assertIsNone(result.objectives[0].value)
        self.assertIn("sample statistics unavailable", result.objectives[0].unavailable_reason)
        json.dumps(result.to_dict(), allow_nan=False)


class CallerTests(unittest.TestCase):
    def test_benchmark_retains_inputs_failures_raw_samples_and_versions(self):
        report = run_benchmark()
        json.dumps(report, allow_nan=False)
        self.assertEqual(report["summary"]["validity_counts"], {"valid": 1, "invalid": 4, "unavailable": 3})
        self.assertEqual(len(report["code_revision"]), 40)
        for case in report["validity_cases"]:
            restored = StateRecord.from_dict(case["input"])
            self.assertEqual(restored.digest, case["result"]["candidate_digest"])
        for run in report["synthetic_runs"]:
            self.assertEqual(len(run["raw_samples"]), 2048)
            self.assertEqual(set(run["raw_samples"]), {8, 12})
            self.assertEqual(fmean(run["raw_samples"]), run["result"]["objectives"][0]["statistics"]["mean"])


if __name__ == "__main__":
    unittest.main()
