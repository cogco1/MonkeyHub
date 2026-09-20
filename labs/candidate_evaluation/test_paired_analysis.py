"""Observable pairing, uncertainty and retained-input integrity checks."""

from copy import deepcopy
import csv
import gzip
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .allocation_benchmark import aggregate, run_trial, synthetic_fixtures
from .paired_analysis import (InputIntegrityError, VERSIONS, analyze, clopper_pearson,
                             compare, paired_pcs_interval, read_experiment)


class PairedAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = synthetic_fixtures()[0]
        cls.rows = [run_trial(fixture, policy=policy, budget=100, budget_unit="samples",
                             repetition=rep, master_seed=11)
                    for policy in ("equal", "ocba") for rep in range(4)]

    def write_run(self, directory, rows=None, *, change_manifest=None):
        rows = deepcopy(self.rows if rows is None else rows)
        with gzip.open(directory / "trials.jsonl.gz", "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        manifest = {**VERSIONS, "code_revision": "test-source", "master_seed": 11,
                    "warmup_successes": 5, "repetitions": 4,
                    "results": [aggregate([row for row in self.rows if row["policy"] == policy])
                                for policy in ("equal", "ocba")]}
        if change_manifest:
            change_manifest(manifest)
        (directory / "summary.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_complete_run_has_verified_pairing_and_hash(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory)
            rows, metadata = read_experiment(directory)
            self.assertEqual(metadata["trial_count"], 8)
            self.assertEqual(len(metadata["raw_sha256"]), 64)
            result = compare(rows, bootstrap_draws=200)[0]
            self.assertEqual(result["paired_trials"], 4)
            self.assertEqual(result["mean_delta_normalized_compute_cost"], 0.)

    def test_truncated_gzip_is_rejected_even_when_all_json_lines_exist(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory)
            raw = directory / "trials.jsonl.gz"
            raw.write_bytes(raw.read_bytes()[:-8])
            with self.assertRaisesRegex(InputIntegrityError, "EOFError"):
                read_experiment(directory)

    def test_duplicate_trial_is_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory, [*self.rows, self.rows[0]])
            with self.assertRaisesRegex(InputIntegrityError, "duplicate trial"):
                read_experiment(directory)

    def test_missing_repetition_is_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory, self.rows[:-1])
            with self.assertRaisesRegex(InputIntegrityError, "repetition"):
                read_experiment(directory)

    def test_missing_condition_is_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory, self.rows[:4])
            with self.assertRaisesRegex(InputIntegrityError, "conditions"):
                read_experiment(directory)

    def test_source_versions_must_match(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory, change_manifest=lambda m: m.update(sampler_version="other-sampler"))
            with self.assertRaisesRegex(InputIntegrityError, "sampler_version"):
                read_experiment(directory)

    def test_aggregate_corruption_is_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory, change_manifest=lambda m: m["results"][0].update(pcs=.123))
            with self.assertRaisesRegex(InputIntegrityError, "raw/aggregate mismatch"):
                read_experiment(directory)

    def test_missing_manifest_does_not_invent_source_version(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_run(directory)
            manifest = json.loads((directory / "summary.json").read_text())
            (directory / "summary.json").unlink()
            with self.assertRaisesRegex(InputIntegrityError, "manifest missing"):
                read_experiment(directory)
            csv_path = directory / "reference.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(manifest["results"][0]))
                writer.writeheader()
                writer.writerows(manifest["results"])
            _, metadata = read_experiment(directory, reference_csv=csv_path)
            self.assertIsNone(metadata["source_revision"])
            self.assertIsNone(metadata["sampler_version"])

    def test_same_repetition_is_insufficient_without_matching_streams(self):
        rows = deepcopy(self.rows)
        rows[-1]["candidate_seeds"][0] += 1
        with self.assertRaisesRegex(InputIntegrityError, "candidate_seeds"):
            compare(rows, bootstrap_draws=200)

    def test_missing_seeds_are_rejected(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rows = deepcopy(self.rows)
            rows[0].pop("candidate_seeds")
            self.write_run(directory, rows)
            with self.assertRaises(InputIntegrityError):
                read_experiment(directory)

    def test_unselected_is_pcs_failure_and_not_zero_regret(self):
        rows = deepcopy(self.rows)
        rows[4].update(selected_candidate=None, correct_selection=False, simple_regret=None)
        result = compare(rows, bootstrap_draws=200)[0]
        self.assertEqual(result["policy_unselected"], 1)
        self.assertEqual(result["regret_jointly_selected_trials"], 3)
        self.assertEqual(result["paired_trials"], 4)
        self.assertEqual(result["delta_pcs"], -.25)

    def test_paid_failures_remain_reported(self):
        rows = deepcopy(self.rows)
        for row in rows:
            row["failures"] = 3 if row["policy"] == "ocba" else 1
        result = compare(rows, bootstrap_draws=200)[0]
        self.assertEqual(result["mean_policy_failures"], 3)
        self.assertEqual(result["mean_equal_failures"], 1)
        self.assertEqual(result["mean_delta_failures"], 2)

    def test_zero_discordance_interval_is_nonzero(self):
        low, high = paired_pcs_interval(0, 0, 200)
        self.assertAlmostEqual(high, 1 - .0125 ** (1 / 200), places=12)
        self.assertAlmostEqual(low, -high, places=12)
        self.assertGreater(high, 0)
        result = compare(self.rows, bootstrap_draws=200)[0]
        self.assertIsNone(result["delta_regret_ci95_low"])
        self.assertIn("degenerate", result["regret_interval_status"])

    def test_exact_binomial_boundaries(self):
        self.assertAlmostEqual(clopper_pearson(0, 1)[1], .975, places=13)
        self.assertAlmostEqual(clopper_pearson(1, 1)[0], .025, places=13)
        low, high = clopper_pearson(5, 10)
        self.assertAlmostEqual(low, 1 - high, places=12)
        self.assertLess(low, .5)
        self.assertGreater(high, .5)

    def test_small_multinomial_enumeration_covers_true_paired_difference(self):
        n = 6
        for pwin, ploss in ((.05, .05), (.15, .35), (.7, .1), (.95, .05)):
            covered = 0.
            for wins in range(n + 1):
                for losses in range(n - wins + 1):
                    ties = n - wins - losses
                    probability = (math.factorial(n) / math.factorial(wins) / math.factorial(losses)
                                   / math.factorial(ties) * pwin ** wins * ploss ** losses
                                   * max(0., 1 - pwin - ploss) ** ties)
                    low, high = paired_pcs_interval(wins, losses, n)
                    if low <= pwin - ploss <= high:
                        covered += probability
            self.assertGreaterEqual(covered, .95 - 1e-12)

    def test_simultaneous_intervals_are_no_narrower(self):
        pointwise = paired_pcs_interval(15, 5, 200)
        simultaneous = paired_pcs_interval(15, 5, 200, .05 / 39)
        self.assertLessEqual(simultaneous[0], pointwise[0])
        self.assertGreaterEqual(simultaneous[1], pointwise[1])

    def test_invalid_data_creates_no_output(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source"
            source.mkdir()
            self.write_run(source, self.rows[:-1])
            output = directory / "output"
            with self.assertRaises(InputIntegrityError):
                analyze(source, output, bootstrap_draws=200)
            self.assertFalse(output.exists())

    def test_outputs_never_overwrite_original_or_existing_results(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source"
            source.mkdir()
            self.write_run(source)
            before = (source / "trials.jsonl.gz").read_bytes()
            with self.assertRaises(ValueError):
                analyze(source, source, bootstrap_draws=200)
            output = directory / "output"
            analyze(source, output, bootstrap_draws=200)
            with self.assertRaises(FileExistsError):
                analyze(source, output, bootstrap_draws=200)
            self.assertEqual(before, (source / "trials.jsonl.gz").read_bytes())


if __name__ == "__main__":
    unittest.main()
