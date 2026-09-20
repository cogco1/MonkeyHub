"""Focused repeat experiments retain complete raw data and the chosen conditions."""
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .allocation_benchmark import run_benchmark


class SelectionTests(unittest.TestCase):
    def test_cost_only_selection_is_paired_complete_and_not_overwritten(self):
        with TemporaryDirectory() as root:
            output = Path(root) / "run"
            args = dict(repetitions=2, master_seed=91, budgets=(4,), include_production=False,
                        fixture_names=("heterogeneous_cost",), policies=("equal", "cost_ocba"),
                        budget_units=("cost",))
            summary = run_benchmark(output, **args)
            self.assertEqual(len(summary["results"]), 2)
            self.assertEqual({row["budget"] for row in summary["results"]}, {20})
            self.assertEqual(summary["selected_policies"], ("equal", "cost_ocba"))
            with gzip.open(output / "trials.jsonl.gz", "rt", encoding="utf-8") as raw:
                trials = [json.loads(line) for line in raw]
            self.assertEqual(len(trials), 4)
            self.assertEqual(trials[0]["candidate_seeds"], trials[2]["candidate_seeds"])
            self.assertEqual(trials[1]["candidate_seeds"], trials[3]["candidate_seeds"])
            self.assertEqual({row["budget_unit"] for row in trials}, {"cost"})
            with self.assertRaises(FileExistsError):
                run_benchmark(output, **args)

    def test_bad_or_incompatible_selectors_fail_without_raw_output(self):
        cases = ({"fixture_names": ("typo",)}, {"policies": ("equal", "equal")},
                 {"fixture_names": ("easy",), "budget_units": ("cost",)},
                 {"policies": ("cost_ocba",), "budget_units": ("samples",)})
        for extra in cases:
            with self.subTest(extra=extra), TemporaryDirectory() as root:
                output = Path(root) / "run"
                with self.assertRaises(ValueError):
                    run_benchmark(output, repetitions=2, master_seed=91, budgets=(4,),
                                  include_production=False, **extra)
                self.assertFalse((output / "trials.jsonl.gz").exists())


if __name__ == "__main__":
    unittest.main()
