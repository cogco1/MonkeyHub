"""The daily benchmark's results: their statistics, budgets, trend and data branch (GH-547).

    python tools/benchmarks/benchmark_data.py publish --repo <checkout> --data-dir <new dir> \\
        --result <result.json> [--result ...] [--budgets <budgets.json>] [--push] \\
        [--flags-out <flags.json>] [--issues-out <issues.json>] [--run-url <url>]
    python tools/benchmarks/benchmark_data.py summary --result <result.json> [--budgets <budgets.json>]

``daily_benchmark.py`` measures and writes one ``MonkeyHubBenchmark@1`` file per
run. This module reads such files and owns everything after the measurement:

- the median and p90 of a metric's samples;
- whether a metric is flagged: over its budget, or its median both more than
  20% and more than 200 ms slower than the trailing 7-day median of the same
  runner (a metric that is not a time is judged on the 20% alone);
- the job summary table, ``trend.md`` (the last 30 runs per metric and runner)
  and ``latest.json``;
- the orphan branch ``benchmark-data``: ``publish`` checks it out as a worktree
  of ``--repo`` (or starts it, unborn, when the remote has none), adds each
  result as ``results/<runner>/<YYYY-MM-DD>-<sha12>.json``, regenerates the
  trend and ``latest.json``, commits and, with ``--push``, pushes. A push that
  loses a race starts again from the remote branch.

Nothing here measures, and nothing but ``publish`` writes into a checkout.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

RESULT_SCHEMA = "MonkeyHubBenchmark@1"
BUDGETS_SCHEMA = "MonkeyHubBenchmarkBudgets@1"
LATEST_SCHEMA = "MonkeyHubBenchmarkLatest@1"
DATA_BRANCH = "benchmark-data"
# Units whose values are times, in milliseconds per unit; the 200 ms floor of
# the regression rule applies to these only.
TIME_UNITS = {"ms": 1.0, "s": 1000.0}
REGRESSION_RATIO = 0.20
REGRESSION_FLOOR_MS = 200.0
REGRESSION_DAYS = 7
TREND_RUNS = 30
SPARKS = "▁▂▃▄▅▆▇█"
DEFAULT_BUDGETS = Path(__file__).resolve().parent / "budgets.json"
README = """# benchmark-data

Results of MonkeyHub's daily benchmark (GH-547), written by
`tools/benchmarks/benchmark_data.py publish` from `.github/workflows/benchmark.yml`.
This branch is an orphan: it shares no history with `main` and is never merged.

- `results/<runner>/<YYYY-MM-DD>-<sha12>.json`: one `MonkeyHubBenchmark@1` file per run.
- `trend.md`: the last 30 runs of every metric, per runner.
- `latest.json`: the newest run of every runner.

What is measured, and how budgets and regressions are judged:
`docs/development/benchmarks.md` on `main`.
"""


class BenchmarkDataError(RuntimeError):
    """A result, a budget file or the data branch that cannot be used as asked."""


# ---- statistics --------------------------------------------------------------


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear interpolation between the closest ranks, as numpy's default does."""

    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rounded(value: float | None, unit: str) -> float | int | None:
    if value is None:
        return None
    if unit == "bytes":
        return int(round(value))
    return round(value, 1 if unit == "ms" else 3)


def metric_entry(samples: Sequence[float], unit: str) -> dict[str, Any]:
    """One metric of a result: its samples, median and p90 in ``unit``."""

    kept = [_rounded(value, unit) for value in samples]
    return {
        "unit": unit,
        "samples": kept,
        "median": _rounded(statistics.median(samples), unit) if samples else None,
        "p90": _rounded(percentile(samples, 0.9), unit),
    }


# ---- results -----------------------------------------------------------------


def load_result(path: Path) -> dict[str, Any]:
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    check_result(result, str(path))
    return result


def check_result(result: Mapping[str, Any], where: str = "result") -> None:
    if result.get("schema") != RESULT_SCHEMA:
        raise BenchmarkDataError(f"{where} is not {RESULT_SCHEMA}")
    commit = result.get("commit")
    if not isinstance(commit, str) or len(commit) < 12:
        raise BenchmarkDataError(f"{where} names no commit")
    _date(result)
    if not isinstance((result.get("runner") or {}).get("key"), str):
        raise BenchmarkDataError(f"{where} names no runner key")
    if not isinstance(result.get("metrics"), dict):
        raise BenchmarkDataError(f"{where} has no metrics")


def _date(result: Mapping[str, Any]) -> datetime:
    value = result.get("date")
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise BenchmarkDataError(f"date {value!r} is not an ISO 8601 time") from error
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def result_path(result: Mapping[str, Any]) -> str:
    """Where a result lives on the data branch."""

    return f"results/{result['runner']['key']}/{_date(result).strftime('%Y-%m-%d')}-{result['commit'][:12]}.json"


# ---- budgets and regressions ---------------------------------------------------


def load_budgets(path: Path | None = DEFAULT_BUDGETS) -> dict[str, Any]:
    if path is None:
        return {}
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != BUDGETS_SCHEMA or not isinstance(document.get("budgets"), dict):
        raise BenchmarkDataError(f"{path} is not {BUDGETS_SCHEMA}")
    return document["budgets"]


def override_budgets(budgets: Mapping[str, Any], overrides: Iterable[str]) -> dict[str, Any]:
    """``metric=value`` pairs replace those metrics' budgets for this run only."""

    changed = dict(budgets)
    for override in overrides:
        metric, separator, value = override.partition("=")
        if not separator or not metric.strip():
            raise BenchmarkDataError(f"budget override {override!r} is not metric=value")
        try:
            changed[metric.strip()] = float(value)
        except ValueError as error:
            raise BenchmarkDataError(f"budget override {override!r} has no number") from error
    return changed


def budget_for(budgets: Mapping[str, Any], metric: str, runner: str) -> float | None:
    """A metric's budget: one number for every runner, or ``{runner: number, "default": number}``."""

    value = budgets.get(metric)
    if isinstance(value, Mapping):
        value = value.get(runner, value.get("default"))
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def trailing_median(history: Iterable[Mapping[str, Any]], metric: str, runner: str, before: datetime,
                    days: int = REGRESSION_DAYS) -> tuple[float | None, int]:
    """The median of a metric's medians over the runner's runs in the ``days`` before ``before``."""

    since = before - timedelta(days=days)
    values = []
    for result in history:
        if result["runner"]["key"] != runner or not since <= _date(result) < before:
            continue
        median = (result["metrics"].get(metric) or {}).get("median")
        if isinstance(median, (int, float)):
            values.append(float(median))
    return (statistics.median(values) if values else None), len(values)


def regressed(median: float, baseline: float, unit: str, *, ratio: float = REGRESSION_RATIO,
              floor_ms: float = REGRESSION_FLOOR_MS) -> bool:
    """Both more than ``ratio`` and, for a time, more than ``floor_ms`` above the baseline."""

    if median <= baseline * (1.0 + ratio):
        return False
    scale = TIME_UNITS.get(unit)
    return scale is None or (median - baseline) * scale > floor_ms


def evaluate(result: Mapping[str, Any], history: Iterable[Mapping[str, Any]], budgets: Mapping[str, Any], *,
             days: int = REGRESSION_DAYS, ratio: float = REGRESSION_RATIO,
             floor_ms: float = REGRESSION_FLOOR_MS) -> list[dict[str, Any]]:
    """Every metric of ``result`` that is over its budget or slower than its runner's trailing median.

    ``history`` may include ``result`` itself and other runners' results: only
    the same runner's runs strictly before this one, within ``days``, count.
    """

    runner = result["runner"]["key"]
    at = _date(result)
    history = list(history)
    flags = []
    for metric, entry in result["metrics"].items():
        median = entry.get("median")
        if not isinstance(median, (int, float)):
            continue
        unit = entry.get("unit", "")
        reasons = []
        budget = budget_for(budgets, metric, runner)
        if budget is not None and median > budget:
            reasons.append("over_budget")
        baseline, runs = trailing_median(history, metric, runner, at, days)
        if baseline is not None and regressed(float(median), baseline, unit, ratio=ratio, floor_ms=floor_ms):
            reasons.append("regression")
        if reasons:
            flags.append({
                "metric": metric, "runner": runner, "unit": unit, "median": median, "p90": entry.get("p90"),
                "budget": budget, "baseline": baseline, "baselineRuns": runs, "reasons": reasons,
                "commit": result["commit"], "date": result["date"], "file": result_path(result),
            })
    return flags


# ---- what a person reads ------------------------------------------------------


def _number(value: Any, unit: str = "") -> str:
    if not isinstance(value, (int, float)):
        return "–"
    if unit == "bytes":
        for size, name in ((1 << 30, "GiB"), (1 << 20, "MiB"), (1 << 10, "KiB")):
            if abs(value) >= size:
                return f"{value / size:.1f} {name}"
        return f"{int(value)} B"
    if unit == "s":
        return f"{value:.2f}"
    return f"{value:.0f}" if abs(value) >= 10 else f"{value:.1f}"


def _with_unit(value: Any, unit: str) -> str:
    text = _number(value, unit)
    return text if unit == "bytes" or text == "–" else f"{text} {unit}"


def summary_markdown(result: Mapping[str, Any], budgets: Mapping[str, Any] | None = None) -> str:
    """The job summary: every metric with its budget, then the Modeling openings and any failure."""

    budgets = budgets or {}
    runner = result["runner"]
    settings = result.get("settings", {})
    lines = [
        f"### Daily benchmark: {runner['key']} · `{result['commit'][:12]}`",
        "",
        f"{runner.get('os', '')} {runner.get('osVersion', '')} · Python {runner.get('python', '')} · "
        f"{runner.get('cpu') or 'CPU unknown'} ({runner.get('cpuCount', '?')} logical) · "
        f"{settings.get('samples', '?')} samples · idle {settings.get('idleSeconds', '?')} s · "
        f"projects of {', '.join(str(size) for size in settings.get('sizes', ()))} runs",
        "",
        "| Metric | Unit | Median | p90 | n | Budget | |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for metric, entry in result["metrics"].items():
        unit = entry.get("unit", "")
        budget = budget_for(budgets, metric, runner["key"])
        over = isinstance(entry.get("median"), (int, float)) and budget is not None and entry["median"] > budget
        lines.append(f"| `{metric}` | {unit} | {_number(entry.get('median'), unit)} | {_number(entry.get('p90'), unit)} | "
                     f"{len(entry.get('samples') or ())} | {_number(budget, unit)} | {'⚠ over budget' if over else ''} |")
    for name, opening in (result.get("details", {}).get("openModeling") or {}).items():
        lines += ["", f"**Open Modeling, {name}** (first sample): {opening.get('rounds')} rounds and "
                      f"{_number(opening.get('modelMs'), 'ms')} ms to the model's bytes; every request answered after "
                      f"{_number(opening.get('totalMs'), 'ms')} ms", "",
                  "| Chain | Request | Status | Start ms | ms | Bytes |", "|---|---|---:|---:|---:|---:|"]
        for row in opening.get("requests", ()):
            lines.append(f"| {row['chain']} | `{row['path']}` | {row['status']} | {_number(row['startMs'], 'ms')} | "
                         f"{_number(row['ms'], 'ms')} | {_number(row.get('bytes'), 'bytes')} |")
    failures = result.get("failures") or []
    if failures:
        lines += ["", "**Failures**", ""] + [f"- {row.get('scenario')} {row.get('size') or ''}: {row.get('error')}"
                                               for row in failures]
    return "\n".join(lines) + "\n"


def _sparkline(values: Sequence[float | None]) -> str:
    known = [value for value in values if value is not None]
    if not known:
        return ""
    low, high = min(known), max(known)
    if high == low:
        return "".join(SPARKS[3] if value is not None else " " for value in values)
    return "".join(" " if value is None else SPARKS[round((value - low) / (high - low) * (len(SPARKS) - 1))]
                   for value in values)


def _by_runner(results: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["runner"]["key"]].append(result)
    for rows in grouped.values():
        rows.sort(key=lambda row: (_date(row), row["commit"]))
    return dict(sorted(grouped.items()))


def trend_markdown(results: Iterable[Mapping[str, Any]], budgets: Mapping[str, Any] | None = None,
                   runs: int = TREND_RUNS) -> str:
    """The last ``runs`` runs of every metric, per runner: oldest to newest, by median."""

    budgets = budgets or {}
    lines = ["# MonkeyHub daily benchmark", "",
             f"The last {runs} runs of every metric, per runner, oldest to newest. Each value is a run's median; "
             "the 7-day median is over the runs of the 7 days up to the latest. Generated by "
             "`tools/benchmarks/benchmark_data.py`; see `docs/development/benchmarks.md` on `main`.", ""]
    for runner, rows in _by_runner(results).items():
        shown = rows[-runs:]
        latest = shown[-1]
        metrics: dict[str, str] = {}
        for row in shown:
            for metric, entry in row["metrics"].items():
                metrics.setdefault(metric, entry.get("unit", ""))
        lines += [f"## {runner}", "",
                  f"Latest: {latest['date']} · `{latest['commit'][:12]}` · {len(shown)} of {len(rows)} runs shown", "",
                  "| Metric | Unit | Trend | Latest | 7-day median | Budget | |", "|---|---|---|---:|---:|---:|---|"]
        values_block = []
        for metric, unit in metrics.items():
            values = [(row["metrics"].get(metric) or {}).get("median") for row in shown]
            values = [float(value) if isinstance(value, (int, float)) else None for value in values]
            week = [value for row, value in zip(shown, values)
                    if value is not None and _date(row) >= _date(latest) - timedelta(days=REGRESSION_DAYS)]
            budget = budget_for(budgets, metric, runner)
            over = values[-1] is not None and budget is not None and values[-1] > budget
            lines.append(f"| `{metric}` | {unit} | `{_sparkline(values)}` | {_number(values[-1], unit)} | "
                         f"{_number(statistics.median(week) if week else None, unit)} | {_number(budget, unit)} | "
                         f"{'⚠' if over else ''} |")
            values_block.append(f"| `{metric}` | {' '.join(_number(value, unit) for value in values)} |")
        lines += ["", "<details><summary>Values and runs</summary>", "", "| Metric | Medians, oldest to newest |",
                  "|---|---|", *values_block, "", "| Run | Commit | File |", "|---|---|---|"]
        lines += [f"| {row['date']} | `{row['commit'][:12]}` | `{result_path(row)}` |" for row in shown]
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def latest_document(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The newest run of every runner, with its medians and p90s."""

    runners = {}
    for runner, rows in _by_runner(results).items():
        latest = rows[-1]
        runners[runner] = {
            "file": result_path(latest), "date": latest["date"], "commit": latest["commit"],
            "runner": latest["runner"],
            "metrics": {metric: {key: entry.get(key) for key in ("unit", "median", "p90")}
                        for metric, entry in latest["metrics"].items()},
        }
    return {"schema": LATEST_SCHEMA, "runners": runners}


def issue_plan(flags: Iterable[Mapping[str, Any]], run_url: str | None = None) -> list[dict[str, str]]:
    """One issue per flagged metric, every runner that flagged it in one table.

    The title is the key the workflow finds an open issue by; the body opens an
    issue and is the comment on one already open.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for flag in flags:
        grouped[flag["metric"]].append(flag)
    plan = []
    for metric, rows in sorted(grouped.items()):
        lines = [f"The daily benchmark (#547) flagged `{metric}` on {rows[0]['date'][:10]}.", "",
                 "| Runner | Median | p90 | Budget | 7-day median | Why |", "|---|---:|---:|---:|---:|---|"]
        for row in sorted(rows, key=lambda item: item["runner"]):
            unit = row["unit"]
            why = ", ".join({"over_budget": "over budget", "regression": "slower than the 7-day median"}[reason]
                            for reason in row["reasons"])
            baseline = (f"{_with_unit(row['baseline'], unit)} ({row['baselineRuns']} runs)"
                        if row.get("baseline") is not None else "–")
            lines.append(f"| {row['runner']} | {_with_unit(row['median'], unit)} | {_with_unit(row.get('p90'), unit)} | "
                         f"{_with_unit(row.get('budget'), unit)} | {baseline} | {why} |")
        lines += ["", f"Commit `{rows[0]['commit'][:12]}`"
                      + (f" · [run]({run_url})" if run_url else "")
                      + " · results on `benchmark-data`: " + ", ".join(f"`{row['file']}`" for row in rows),
                  "", "How metrics are measured and judged: `docs/development/benchmarks.md`.", "",
                  f"<!-- monkeyhub-benchmark-metric: {metric} -->"]
        plan.append({"metric": metric, "title": f"perf: {metric} flagged by the daily benchmark",
                     "body": "\n".join(lines) + "\n"})
    return plan


# ---- the data branch ----------------------------------------------------------


def write_file(path: Path, text: str) -> None:
    """The one place the daily benchmark writes a file, as UTF-8 with LF line ends.

    ``daily_benchmark.py`` writes its result, summary and each Hub's launch
    configuration through it; ``publish`` the files of the data worktree it
    checked out and the flags and issues a person names with --*-out.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if check and completed.returncode != 0:
        raise BenchmarkDataError(f"git {' '.join(args)} failed ({completed.returncode}): "
                                 f"{completed.stderr.strip() or completed.stdout.strip()}")
    return completed


def prepare_checkout(repo: Path, data_dir: Path, remote: str = "origin", branch: str = DATA_BRANCH) -> bool:
    """Check the data branch out at ``data_dir``, a new worktree of ``repo``; answer whether it is new.

    The remote's branch is checked out when it exists. Otherwise the worktree
    starts on an unborn branch, so its first commit has no parent: an orphan
    that shares nothing with ``main``.
    """

    if data_dir.exists():
        raise BenchmarkDataError(f"{data_dir} already exists; publish checks the data branch out somewhere new")
    exists = _git(repo, "ls-remote", "--exit-code", "--heads", remote, branch, check=False)
    if exists.returncode not in (0, 2):
        raise BenchmarkDataError(f"cannot ask {remote} for {branch}: {exists.stderr.strip()}")
    if exists.returncode == 0:
        _git(repo, "fetch", "--no-tags", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
        _git(repo, "worktree", "add", "-B", branch, str(data_dir), f"{remote}/{branch}")
        return False
    _git(repo, "worktree", "add", "--orphan", "-b", branch, str(data_dir))
    return True


def read_history(data_dir: Path) -> list[dict[str, Any]]:
    """Every result already on the data branch."""

    return [load_result(path) for path in sorted((data_dir / "results").glob("*/*.json"))]


def add_results(data_dir: Path, results: Sequence[Mapping[str, Any]], budgets: Mapping[str, Any]) -> list[str]:
    """Add ``results`` and regenerate ``trend.md``, ``latest.json`` and the README; answer the paths written.

    A second run of one commit on one day keeps both: the later file takes a
    ``-2``, ``-3`` suffix.
    """

    written = []
    for result in results:
        relative = result_path(result)
        target = data_dir / relative
        number = 2
        while target.exists():
            target = data_dir / relative.replace(".json", f"-{number}.json")
            number += 1
        write_file(target, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        written.append(target.relative_to(data_dir).as_posix())
    history = read_history(data_dir)
    write_file(data_dir / "trend.md", trend_markdown(history, budgets))
    write_file(data_dir / "latest.json", json.dumps(latest_document(history), indent=2, ensure_ascii=False) + "\n")
    write_file(data_dir / "README.md", README)
    return written + ["trend.md", "latest.json", "README.md"]


def commit(data_dir: Path, message: str, author: tuple[str, str] | None = None) -> str:
    identity = () if author is None else ("-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}")
    _git(data_dir, "add", "--", "results", "trend.md", "latest.json", "README.md")
    _git(data_dir, *identity, "commit", "--quiet", "-m", message)
    return _git(data_dir, "rev-parse", "HEAD").stdout.strip()


def publish(repo: Path, data_dir: Path, results: Sequence[Mapping[str, Any]], budgets: Mapping[str, Any], *,
            remote: str = "origin", branch: str = DATA_BRANCH, push: bool = False,
            author: tuple[str, str] | None = None, attempts: int = 3,
            judged_by: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate ``results`` against the branch's history, add them, commit and optionally push.

    The flags are judged against the history read before these results were
    added, by ``judged_by`` when given (a run's budget overrides) and by
    ``budgets`` otherwise; ``trend.md`` always shows ``budgets``. A rejected
    push fetches the remote branch, moves the worktree onto it and adds the
    same results again.
    """

    created = prepare_checkout(repo, data_dir, remote, branch)
    history = read_history(data_dir)
    flags = [flag for result in results for flag in evaluate(result, history, budgets if judged_by is None else judged_by)]
    keys = sorted({result["runner"]["key"] for result in results})
    commits = sorted({result["commit"][:12] for result in results})
    message = f"benchmark: {', '.join(keys)} at {', '.join(commits)}"
    for attempt in range(1, attempts + 1):
        written = add_results(data_dir, results, budgets)
        head = commit(data_dir, message, author)
        if not push:
            break
        pushed = _git(data_dir, "push", remote, f"HEAD:refs/heads/{branch}", check=False)
        if pushed.returncode == 0:
            break
        if attempt == attempts:
            raise BenchmarkDataError(f"push to {remote}/{branch} failed {attempts} times: {pushed.stderr.strip()}")
        _git(repo, "fetch", "--no-tags", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
        # Only this run's own commit, never pushed, is left behind.
        _git(data_dir, "reset", "--keep", f"{remote}/{branch}")
    return {"created": created, "commit": head, "written": written, "flags": flags, "pushed": push}


# ---- command line -------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    publish_command = commands.add_parser("publish", help="add results to the data branch and judge them")
    publish_command.add_argument("--repo", type=Path, required=True, help="a checkout whose remote has the data branch")
    publish_command.add_argument("--data-dir", type=Path, required=True, help="where to check the data branch out (new)")
    publish_command.add_argument("--result", type=Path, action="append", required=True)
    publish_command.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    publish_command.add_argument("--budget-override", action="append", default=[], metavar="METRIC=VALUE")
    publish_command.add_argument("--remote", default="origin")
    publish_command.add_argument("--branch", default=DATA_BRANCH)
    publish_command.add_argument("--push", action="store_true")
    publish_command.add_argument("--author", nargs=2, metavar=("NAME", "EMAIL"))
    publish_command.add_argument("--flags-out", type=Path, help="where to write the flagged metrics (JSON)")
    publish_command.add_argument("--issues-out", type=Path, help="where to write one issue per flagged metric (JSON)")
    publish_command.add_argument("--run-url", help="the workflow run, linked from the issues")
    summary_command = commands.add_parser("summary", help="print one result's summary table")
    summary_command.add_argument("--result", type=Path, required=True)
    summary_command.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    summary_command.add_argument("--budget-override", action="append", default=[], metavar="METRIC=VALUE")
    options = parser.parse_args(argv)
    # A console that cannot show a sign gets a placeholder, not a traceback at the end of a run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    budgets = load_budgets(options.budgets)
    judged_by = override_budgets(budgets, options.budget_override)
    if options.command == "summary":
        sys.stdout.write(summary_markdown(load_result(options.result), judged_by))
        return 0
    results = [load_result(path) for path in options.result]
    outcome = publish(options.repo.resolve(), options.data_dir.resolve(), results, budgets, remote=options.remote,
                      branch=options.branch, push=options.push, author=tuple(options.author) if options.author else None,
                      judged_by=judged_by)
    if options.flags_out:
        write_file(options.flags_out, json.dumps(outcome["flags"], indent=2, ensure_ascii=False) + "\n")
    if options.issues_out:
        write_file(options.issues_out, json.dumps(issue_plan(outcome["flags"], options.run_url), indent=2,
                                              ensure_ascii=False) + "\n")
    sys.stdout.write(f"{'Started' if outcome['created'] else 'Updated'} {options.branch} at {outcome['commit'][:12]}"
                     f"{' and pushed it' if outcome['pushed'] else ''}: {', '.join(outcome['written'])}\n")
    for flag in outcome["flags"]:
        sys.stdout.write(f"flagged {flag['metric']} on {flag['runner']}: {', '.join(flag['reasons'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
