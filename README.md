# benchmark-data

Results of MonkeyHub's daily benchmark (GH-547), written by
`tools/benchmarks/benchmark_data.py publish` from `.github/workflows/benchmark.yml`.
This branch is an orphan: it shares no history with `main` and is never merged.

- `results/<runner>/<YYYY-MM-DD>-<sha12>.json`: one `MonkeyHubBenchmark@1` file per run.
- `trend.md`: the last 30 runs of every metric, per runner.
- `latest.json`: the newest run of every runner.

What is measured, and how budgets and regressions are judged:
`docs/development/benchmarks.md` on `main`.
