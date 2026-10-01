"""The daily benchmark's statistics, judgement, trend and data branch (GH-547)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.benchmarks import benchmark_data as data


def result(date: str, commit: str = "a" * 40, runner: str = "linux", **medians: float) -> dict:
    """A fabricated MonkeyHubBenchmark@1 result whose metrics have the given medians (ms unless named)."""

    metrics = {}
    for name, median in medians.items():
        metric = name.replace("__", ".")
        unit = "bytes" if metric.endswith(".read") else "s" if metric.endswith(".cpu") else "ms"
        metrics[metric] = {"unit": unit, "samples": [median], "median": median, "p90": median}
    return {"schema": data.RESULT_SCHEMA, "commit": commit, "date": date,
            "runner": {"key": runner, "os": runner, "python": "3.12.10", "cpu": "test", "cpuCount": 4},
            "settings": {"samples": 1, "idleSeconds": 60, "sizes": [30]}, "metrics": metrics, "details": {},
            "failures": []}


class StatisticsTests(unittest.TestCase):
    def test_median_and_p90_interpolate_between_ranks(self):
        entry = data.metric_entry([10.0, 30.0, 20.0, 50.0, 40.0], "ms")
        self.assertEqual(entry["median"], 30.0)
        self.assertEqual(entry["p90"], 46.0)  # 40 + 0.6 * (50 - 40)
        self.assertEqual(entry["samples"], [10.0, 30.0, 20.0, 50.0, 40.0])

    def test_units_round_as_their_values_need(self):
        self.assertEqual(data.metric_entry([1234.5678], "ms")["median"], 1234.6)
        self.assertEqual(data.metric_entry([0.123456], "s")["median"], 0.123)
        self.assertEqual(data.metric_entry([1000.6, 2000.2], "bytes")["samples"], [1001, 2000])

    def test_no_samples_have_no_median(self):
        self.assertEqual(data.metric_entry([], "ms"), {"unit": "ms", "samples": [], "median": None, "p90": None})


class SchemaTests(unittest.TestCase):
    def test_a_result_names_its_schema_commit_date_runner_and_metrics(self):
        data.check_result(result("2026-10-01T20:30:00Z", hub_start__warm=1000.0))
        for broken in ({"schema": "Other@1"}, {"commit": "abc"}, {"date": "yesterday"}, {"runner": {}},
                       {"metrics": None}):
            with self.subTest(broken=broken), self.assertRaises(data.BenchmarkDataError):
                data.check_result({**result("2026-10-01T20:30:00Z"), **broken})

    def test_a_result_lives_under_its_runner_by_date_and_commit(self):
        self.assertEqual(data.result_path(result("2026-10-01T20:30:00Z", "0123456789abcdef" * 2 + "01234567")),
                         "results/linux/2026-10-01-0123456789ab.json")


class EvaluationTests(unittest.TestCase):
    def history(self, runner: str = "linux", **medians: float) -> list[dict]:
        return [result(f"2026-09-{day:02d}T20:30:00Z", runner=runner, **medians) for day in range(24, 31)]

    def test_over_budget_is_flagged_without_any_history(self):
        flags = data.evaluate(result("2026-10-01T20:30:00Z", hub_start__warm=2600.0), [], {"hub_start.warm": 2500})
        self.assertEqual([(flag["metric"], flag["reasons"]) for flag in flags], [("hub_start.warm", ["over_budget"])])
        self.assertEqual(flags[0]["budget"], 2500.0)
        self.assertIsNone(flags[0]["baseline"])

    def test_a_time_regresses_only_when_both_20_percent_and_200_ms_slower(self):
        history = self.history(project_open__30runs=1000.0, route__board__cold__30runs=100.0)
        cases = {
            # 25% and 250 ms slower: flagged.
            ("project_open.30runs", 1250.0): True,
            # 25% but only 25 ms slower: a fast route is not flagged for noise.
            ("route.board.cold.30runs", 125.0): False,
            # 210 ms but only 21% of 1000: flagged; 19.9% is not.
            ("project_open.30runs", 1210.0): True,
            ("project_open.30runs", 1199.0): False,
        }
        for (metric, median), flagged in cases.items():
            with self.subTest(metric=metric, median=median):
                today = result("2026-10-01T20:30:00Z", **{metric.replace(".", "__"): median})
                flags = data.evaluate(today, history, {})
                self.assertEqual(bool(flags), flagged)
                if flagged:
                    self.assertEqual(flags[0]["reasons"], ["regression"])
                    self.assertEqual(flags[0]["baseline"], history[0]["metrics"][metric]["median"])
                    self.assertEqual(flags[0]["baselineRuns"], 7)

    def test_cpu_seconds_are_a_time_and_bytes_regress_by_20_percent_and_1_mib(self):
        history = self.history(idle__hub__cpu=0.5, idle__worker__cpu=0.5, idle__hub__read=1000.0,
                               idle__worker__read=4.0 * 2**20)
        today = result("2026-10-01T20:30:00Z", idle__hub__cpu=0.65, idle__worker__cpu=0.75,
                       idle__hub__read=1300.0, idle__worker__read=5.5 * 2**20)
        # CPU: 0.15 s more is 30% but under the 200 ms floor; 0.25 s more is over both.
        # Bytes: 300 more is 30% but under the 1 MiB floor; 1.5 MiB more is 37.5% and over it.
        self.assertEqual([flag["metric"] for flag in data.evaluate(today, history, {})],
                         ["idle.worker.cpu", "idle.worker.read"])
        cases = {(2**20, 2 * 2**20): False,  # 100% but exactly 1 MiB more: not more than the floor
                 (2**20, 2 * 2**20 + 1): True,
                 (10 * 2**20, 11.5 * 2**20): False,  # 1.5 MiB more but only 15%
                 (0.0, 1.5 * 2**20): True}
        for (baseline, median), flagged in cases.items():
            with self.subTest(baseline=baseline, median=median):
                self.assertEqual(data.regressed(median, baseline, "bytes"), flagged)

    def test_only_the_same_runners_runs_of_the_seven_days_before_count(self):
        history = [
            result("2026-09-23T20:29:59Z", project_open__30runs=100.0),  # 8 days before: too old
            result("2026-09-30T20:30:00Z", runner="windows", project_open__30runs=100.0),  # another runner
            result("2026-10-01T20:30:00Z", project_open__30runs=100.0),  # this very run
            result("2026-10-02T20:30:00Z", project_open__30runs=100.0),  # later
            result("2026-09-25T20:30:00Z", project_open__30runs=2000.0),  # the one that counts
        ]
        flags = data.evaluate(result("2026-10-01T20:30:00Z", project_open__30runs=2100.0), history, {})
        self.assertEqual(flags, [])
        self.assertEqual(data.trailing_median(history, "project_open.30runs", "linux",
                                              data._date(result("2026-10-01T20:30:00Z"))), (2000.0, 1))

    def test_a_budget_may_differ_by_runner_and_be_overridden_for_one_run(self):
        budgets = {"hub_start.warm": {"windows": 5000, "default": 2500}, "project_open.30runs": 4000}
        self.assertEqual(data.budget_for(budgets, "hub_start.warm", "windows"), 5000.0)
        self.assertEqual(data.budget_for(budgets, "hub_start.warm", "linux"), 2500.0)
        self.assertIsNone(data.budget_for(budgets, "route.board.cold.30runs", "linux"))
        lowered = data.override_budgets(budgets, ["project_open.30runs=1"])
        self.assertEqual(lowered["project_open.30runs"], 1.0)
        self.assertEqual(budgets["project_open.30runs"], 4000)
        for bad in ("project_open.30runs", "=3", "project_open.30runs=fast"):
            with self.subTest(bad=bad), self.assertRaises(data.BenchmarkDataError):
                data.override_budgets(budgets, [bad])

    def test_both_reasons_are_given_together(self):
        flags = data.evaluate(result("2026-10-01T20:30:00Z", hub_start__warm=5000.0),
                              self.history(hub_start__warm=1000.0), {"hub_start.warm": 2500})
        self.assertEqual(flags[0]["reasons"], ["over_budget", "regression"])


class ReadingTests(unittest.TestCase):
    def test_the_summary_marks_a_metric_over_its_budget(self):
        text = data.summary_markdown(result("2026-10-01T20:30:00Z", hub_start__warm=2600.0, project_open__30runs=10.0),
                                     {"hub_start.warm": 2500})
        row = next(line for line in text.splitlines() if "`hub_start.warm`" in line)
        self.assertIn("2500", row)
        self.assertIn("over budget", row)
        self.assertNotIn("over budget", next(line for line in text.splitlines() if "project_open" in line))

    def test_the_trend_shows_the_last_30_runs_per_runner_oldest_first(self):
        runs = [result(f"2026-09-{day:02d}T20:30:00Z", commit=f"{day:02d}" * 20, hub_start__warm=float(day))
                for day in range(1, 31)]
        runs += [result("2026-10-01T20:30:00Z", commit="ff" * 20, hub_start__warm=31.0),
                 result("2026-10-01T20:31:00Z", runner="windows", hub_start__warm=5.0)]
        text = data.trend_markdown(runs, {"hub_start.warm": 30})
        linux = text.split("## linux")[1].split("## windows")[0]
        self.assertIn("30 of 31 runs shown", linux)
        row = next(line for line in linux.splitlines() if line.startswith("| `hub_start.warm` | ms |"))
        spark = row.split("`")[3]
        self.assertEqual((len(spark), spark[0], spark[-1]), (30, data.SPARKS[0], data.SPARKS[-1]))
        self.assertIn("| 31 |", row)
        self.assertIn("⚠", row)
        values = next(line for line in linux.splitlines() if line.startswith("| `hub_start.warm` | 2.0"))
        self.assertTrue(values.endswith("31 |"))
        self.assertIn("## windows", text)

    def test_latest_names_each_runners_newest_run(self):
        document = data.latest_document([result("2026-09-30T20:30:00Z", commit="1" * 40, hub_start__warm=2.0),
                                         result("2026-10-01T20:30:00Z", commit="2" * 40, hub_start__warm=1.0)])
        self.assertEqual(document["schema"], data.LATEST_SCHEMA)
        self.assertEqual(document["runners"]["linux"]["commit"], "2" * 40)
        self.assertEqual(document["runners"]["linux"]["metrics"]["hub_start.warm"], {"unit": "ms", "median": 1.0, "p90": 1.0})

    def test_one_issue_per_metric_holds_every_runner_that_flagged_it(self):
        flags = [
            {**flag, "runner": runner} for runner in ("windows", "linux")
            for flag in data.evaluate(result("2026-10-01T20:30:00Z", runner=runner, hub_start__warm=3000.0), [],
                                      {"hub_start.warm": 2500})
        ]
        plan = data.issue_plan(flags, "https://example.invalid/run/1")
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["title"], "perf: hub_start.warm flagged by the daily benchmark")
        body = plan[0]["body"]
        self.assertLess(body.index("| linux |"), body.index("| windows |"))
        self.assertIn("over budget", body)
        self.assertIn("<!-- monkeyhub-benchmark-metric: hub_start.warm -->", body)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


class DataBranchTests(unittest.TestCase):
    """``publish`` against a temporary bare repository: never the project's own remote."""

    author = ("Benchmark test", "benchmark-test@example.invalid")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote)], check=True)
        self.repo = self.clone("checkout")
        (self.repo / "README.md").write_text("main\n", encoding="utf-8")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "--quiet", "-m", "main")
        _git(self.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        self.root = root

    def clone(self, name: str) -> Path:
        target = Path(self.temporary.name) / name
        subprocess.run(["git", "clone", "--quiet", str(self.remote), str(target)], check=True, capture_output=True)
        return target

    def remote_files(self) -> list[str]:
        return sorted(_git(self.remote, "ls-tree", "-r", "--name-only", data.DATA_BRANCH).splitlines())

    def test_the_first_publish_starts_an_orphan_branch_and_pushes_it(self):
        first = result("2026-10-01T20:30:00Z", commit="1" * 40, hub_start__warm=1000.0)
        # A budget lowered for this run flags the metric; the trend keeps showing the file's budget.
        outcome = data.publish(self.repo, self.root / "data-1", [first], {"hub_start.warm": 5000}, push=True,
                               author=self.author, judged_by={"hub_start.warm": 900})
        self.assertTrue(outcome["created"])
        self.assertEqual([flag["reasons"] for flag in outcome["flags"]], [["over_budget"]])
        self.assertEqual(outcome["flags"][0]["budget"], 900.0)
        trend_row = next(line for line in _git(self.remote, "show", f"{data.DATA_BRANCH}:trend.md").splitlines()
                         if line.startswith("| `hub_start.warm` |"))
        self.assertIn("| 5000 |", trend_row)
        self.assertEqual(self.remote_files(), ["README.md", "latest.json",
                                               "results/linux/2026-10-01-111111111111.json", "trend.md"])
        self.assertEqual(_git(self.remote, "rev-list", "--count", data.DATA_BRANCH), "1")
        # An orphan: nothing in common with main.
        merge_base = subprocess.run(["git", "-C", str(self.remote), "merge-base", "main", data.DATA_BRANCH],
                                    capture_output=True, text=True)
        self.assertNotEqual(merge_base.returncode, 0)
        self.assertEqual(_git(self.remote, "log", "-1", "--format=%an", data.DATA_BRANCH), self.author[0])
        stored = json.loads(_git(self.remote, "show", f"{data.DATA_BRANCH}:results/linux/2026-10-01-111111111111.json"))
        self.assertEqual(stored, first)

    def test_a_later_publish_adds_to_the_branch_and_judges_against_it(self):
        data.publish(self.repo, self.root / "data-1", [result("2026-10-01T20:30:00Z", commit="1" * 40,
                                                              hub_start__warm=1000.0)], {}, push=True, author=self.author)
        later = [result("2026-10-02T20:30:00Z", commit="2" * 40, hub_start__warm=1500.0),
                 result("2026-10-02T20:31:00Z", commit="2" * 40, runner="windows", hub_start__warm=1500.0)]
        outcome = data.publish(self.clone("checkout-2"), self.root / "data-2", later, {}, push=True, author=self.author)
        self.assertFalse(outcome["created"])
        self.assertEqual([(flag["runner"], flag["reasons"]) for flag in outcome["flags"]], [("linux", ["regression"])])
        self.assertEqual(_git(self.remote, "rev-list", "--count", data.DATA_BRANCH), "2")
        self.assertIn("results/windows/2026-10-02-222222222222.json", self.remote_files())
        latest = json.loads(_git(self.remote, "show", f"{data.DATA_BRANCH}:latest.json"))
        self.assertEqual(sorted(latest["runners"]), ["linux", "windows"])
        trend = _git(self.remote, "show", f"{data.DATA_BRANCH}:trend.md")
        self.assertIn("2 of 2 runs shown", trend)

    def test_a_second_run_of_one_commit_on_one_day_keeps_both(self):
        one = result("2026-10-01T20:30:00Z", commit="1" * 40, hub_start__warm=1000.0)
        data.publish(self.repo, self.root / "data-1", [one], {}, push=True, author=self.author)
        data.publish(self.clone("checkout-2"), self.root / "data-2", [{**one, "date": "2026-10-01T22:00:00Z"}], {},
                     push=True, author=self.author)
        self.assertIn("results/linux/2026-10-01-111111111111-2.json", self.remote_files())

    def test_a_push_that_loses_a_race_starts_again_from_the_remote_branch(self):
        data.publish(self.repo, self.root / "data-1", [result("2026-10-01T20:30:00Z", commit="1" * 40,
                                                              hub_start__warm=1.0)], {}, push=True, author=self.author)
        real_add = data.add_results
        raced = []

        def add_after_someone_else(data_dir, results, budgets):
            if not raced:
                raced.append(True)
                # Another publisher pushes first, after this one checked the branch out.
                data.publish(self.clone("rival"), self.root / "data-rival",
                             [result("2026-10-02T20:31:00Z", commit="3" * 40, runner="windows", hub_start__warm=1.0)],
                             {}, push=True, author=self.author)
            return real_add(data_dir, results, budgets)

        with mock.patch.object(data, "add_results", side_effect=add_after_someone_else):
            data.publish(self.clone("checkout-2"), self.root / "data-2",
                         [result("2026-10-02T20:30:00Z", commit="2" * 40, hub_start__warm=1.0)], {}, push=True,
                         author=self.author)
        files = self.remote_files()
        self.assertIn("results/windows/2026-10-02-333333333333.json", files)
        self.assertIn("results/linux/2026-10-02-222222222222.json", files)
        self.assertEqual(_git(self.remote, "rev-list", "--count", data.DATA_BRANCH), "3")

    def test_without_push_nothing_reaches_the_remote(self):
        outcome = data.publish(self.repo, self.root / "data-1", [result("2026-10-01T20:30:00Z", hub_start__warm=1.0)],
                               {}, author=self.author)
        self.assertFalse(outcome["pushed"])
        self.assertEqual(_git(self.root / "data-1", "rev-parse", "HEAD"), outcome["commit"])
        branches = subprocess.run(["git", "-C", str(self.remote), "branch", "--list", data.DATA_BRANCH],
                                  capture_output=True, text=True, check=True).stdout
        self.assertEqual(branches.strip(), "")

    def test_publish_refuses_a_directory_that_exists(self):
        (self.root / "taken").mkdir()
        with self.assertRaises(data.BenchmarkDataError):
            data.publish(self.repo, self.root / "taken", [result("2026-10-01T20:30:00Z")], {})


if __name__ == "__main__":
    unittest.main()
