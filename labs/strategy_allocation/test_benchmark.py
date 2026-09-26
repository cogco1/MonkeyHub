"""Whole experiments on the fake runner: forking, retention, records, reproducibility and replay."""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from .benchmark import ExperimentConfig, main, run_experiment, verify
from .environment import Environment
from .rollout import csv_row, read_csv, read_jsonl
from .strategies import FakeReply, FakeRunner, demo_fake_runner, plan_answer, weighted

CASES = ("provided-source", "protected-dependency", "open-direction", "local-conflict")


def config(**overrides):
    values = dict(experiment_id="test", cases=CASES, strategies=("A", "B", "C", "D"), budget_per_case=8)
    return ExperimentConfig(**{**values, **overrides})


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sa268-run-")
        self.root = Path(self.temporary.name)
        self.env = Environment()

    def tearDown(self):
        self.temporary.cleanup()

    def run_fake(self, name="run", runner=None, **overrides):
        out = self.root / name
        return run_experiment(config(**overrides), runner=runner or demo_fake_runner(), out_dir=out,
                              log=lambda line: None), out

    def test_every_rollout_forks_from_its_case_snapshot_in_its_own_session(self):
        records, _ = self.run_fake()
        self.assertEqual(len(records), 32)
        for case_id in CASES:
            mine = [record for record in records if record.case_id == case_id]
            self.assertEqual({record.snapshot_digest for record in mine}, {self.env.reset(case_id).digest})
            for strategy_id in "ABCD":  # one prompt per strategy and snapshot: nothing else leaks in
                self.assertEqual(len({record.prompt_sha256 for record in mine if record.strategy_id == strategy_id}), 1)
        self.assertEqual(len({record.session["sessionId"] for record in records}), 32)

    def test_the_budget_is_spent_exactly_after_a_balanced_initialization(self):
        records, out = self.run_fake(budget_per_case=10)
        steps = [json.loads(line) for line in (out / "allocation.jsonl").read_text(encoding="utf-8").splitlines()]
        for case_id in CASES:
            mine = [step for step in steps if step["caseId"] == case_id]
            self.assertEqual(mine[0]["allocations"], {"A": 1, "B": 1, "C": 1, "D": 1})
            self.assertEqual(mine[-1]["stoppingReason"], "insufficient_budget")
            counts = Counter(record.strategy_id for record in records if record.case_id == case_id)
            self.assertEqual(sum(counts.values()), 10)
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
            self.assertEqual(sorted(mine[0]["order"]), ["A", "B", "C", "D"])

    def test_failures_are_retained_and_count_as_unsuccessful_samples(self):
        runner = FakeRunner({
            "A": lambda request, rng: FakeReply(status="timeout"),
            "B": lambda request, rng: FakeReply({"plan": "Repair"}),
            "C": lambda request, rng: FakeReply(plan_answer(["Repair"])),  # no ranking: policy violation
            "D": lambda request, rng: FakeReply(plan_answer(["Repair"]), output_tokens=5000),
        })
        records, out = self.run_fake(runner=runner, cases=("local-conflict",))
        self.assertEqual({record.strategy_id: record.status for record in records},
                         {"A": "timeout", "B": "malformed", "C": "policy_violation", "D": "token_cap_exceeded"})
        self.assertFalse(any(record.success or record.chosen_actions for record in records))
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual({arm["strategyId"]: (arm["n"], arm["mean"]) for arm in summary["cases"][0]["arms"]},
                         {arm: (2, 0.0) for arm in "ABCD"})
        self.assertEqual(verify(out), [])

    def test_records_round_trip_through_json_lines_and_csv(self):
        records, out = self.run_fake()
        self.assertEqual(read_jsonl(out / "rollouts.jsonl"), records)
        self.assertEqual(read_csv(out / "rollouts.csv"), [csv_row(record) for record in records])

    def test_the_outcome_does_not_depend_on_tokens(self):
        plans = {"A": weighted([(1.0, ["Repair"])]), "B": weighted([(1.0, ["Inspect", "Repair"])]),
                 "C": weighted([(1.0, ["Repair"])], ranked=True), "D": weighted([(1.0, ["ReModel"])])}

        def spending(tokens):
            return FakeRunner({key: (lambda request, rng, behave=behave: FakeReply(behave(request, rng).answer,
                                                                                output_tokens=tokens))
                               for key, behave in plans.items()})

        cheap, _ = self.run_fake("cheap", spending(10), cases=("local-conflict",))
        costly, _ = self.run_fake("costly", spending(3999), cases=("local-conflict",))
        self.assertEqual([record.evaluation for record in cheap], [record.evaluation for record in costly])
        self.assertNotEqual([record.resources for record in cheap], [record.resources for record in costly])

    def test_the_same_seed_reproduces_the_run(self):
        first, _ = self.run_fake("first", seed=7)
        second, _ = self.run_fake("second", seed=7)

        def comparable(records):
            return [{key: value for key, value in record.to_dict().items() if key not in ("started_at",)}
                    for record in records]

        self.assertEqual(comparable(first), comparable(second))

    def test_verify_replays_a_run_and_detects_tampering(self):
        _, out = self.run_fake()
        self.assertEqual(verify(out), [])
        lines = (out / "rollouts.jsonl").read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["evaluation"]["success"] = not record["evaluation"]["success"]
        (out / "rollouts.jsonl").write_text("\n".join([json.dumps(record), *lines[1:]]) + "\n", encoding="utf-8")
        problems = verify(out)
        self.assertTrue(any("re-evaluate" in problem for problem in problems))

    def test_results_are_never_overwritten(self):
        self.run_fake()
        with self.assertRaises(FileExistsError):
            self.run_fake()

    def test_the_cli_refuses_codex_runs_above_the_call_limit(self):
        out = self.root / "refused"
        self.assertEqual(main(["run", "--runner", "codex", "--out", str(out), "--budget", "8"]), 2)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
