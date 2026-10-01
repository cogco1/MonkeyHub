# Daily benchmark

`tools/benchmarks/daily_benchmark.py` measures MonkeyHub from a checkout on the same scenarios every day.
`tools/benchmarks/benchmark_data.py` judges the results against budgets and their own trend, and keeps them on
the orphan branch `benchmark-data` (issue #547). Both run in `.github/workflows/benchmark.yml`:

| When | Runs on | Writes |
|---|---|---|
| Every day at 20:30 UTC, after the 19:00 UTC [nightly promotion](nightly-release.md) | `ubuntu-latest` and `windows-latest` | results to `benchmark-data`; one `type:perf` issue per flagged metric once issues are on ([below](#budgets-and-flags)) |
| **Run workflow** (`workflow_dispatch`) on `main` | both | the same; `samples`, `budget_override` and `issues` change one run |
| A pull request that changes the benchmark's own files: the harness, its budgets, its tests or the workflow | both | nothing: the table goes to the job summary |
| Once a day while the owner's Windows machine is idle, once its task is registered ([local runs](#local-runs)) | the installed MonkeyHub on that machine | results to `results/windows-local/` on `benchmark-data`; no issue |

The workflow is not a required check. Its numbers are a trend, not a gate on any change.

## What is measured

Every Hub the harness starts runs this checkout's `apps/monkeyhub/run.py` on its own port, with its own runtime
root, `APPDATA`, `LOCALAPPDATA` and bytecode prefix under `--work`, and with an account credential store that holds
nothing. `PYTHONPATH` and any `ARCHFLOW_*` or `MONKEYHUB_*` variable are dropped. An installed MonkeyHub's settings,
data, ports and saved keys are never touched. Each project is copied into `--work` once and its file times are
moved back (`projection_check.settle`); no scenario writes to it.

| Metric | What is timed or counted | Unit |
|---|---|---|
| `hub_start.first_launch` | From process start to the first `/api/health` answer, with an empty `PYTHONPYCACHEPREFIX`: every module is compiled. A checkout ships no bytecode; the release package does since #548, and a [local run](#local-runs) measures a new copy of the installed version with it, once per version. | ms |
| `hub_start.warm` | The same, with the bytecode earlier launches left in the prefix; locally, the copy launched before. | ms |
| `project_open.<N>runs` | The requests of the web client's `ensureProject` (`ChatShell.tsx`): open the project's runtime, read `/api/runtime`, start its worker, ask `/api/apps` until the worker runs, read the project binding through the Hub. The harness asks every 25 ms; the page waits 400 ms between asks. | ms |
| `open_modeling.cold.<N>runs` | The requests the Modeling workspace sends when it opens, in the client's order and parallel rounds, through the Hub, until every one has answered. Cold is the first opening on a new worker. | ms |
| `open_modeling.model.cold.<N>runs` | The same opening until the model's bytes have arrived: what the model on screen waits for. | ms |
| `open_modeling.warm.<N>runs`, `open_modeling.model.warm.<N>runs` | The same opening again on that worker, with no browser cache carried over. | ms |
| `route.<route>.cold.<N>runs`, `.warm` | The six routes of the [projection check](projection-check.md), each read through the Hub once on a new worker and once more. | ms |
| `idle.hub.cpu.<N>runs`, `idle.worker.cpu.<N>runs` | CPU seconds over 60 s with the project open, Modeling opened once and the Hub's event stream held, as a page holds it. The window starts once the Hub and the worker have gone quiet after that opening: less than 0.1 CPU s in 2 s, or after 120 s at most. How long that took is in the details. The worker counts with its child processes. | s |
| `idle.hub.read.<N>runs`, `idle.worker.read.<N>runs` | Bytes read in the same window. Linux counts every read call (`rchar`: files, sockets and the page cache); Windows counts the process's read transfers (`ReadTransferCount`), which leaves socket reads out. | bytes |

`<N>` is the project size. Every scenario runs at **30 runs**, the projection check's project, and at **150 runs**:
five times as large, and three times the 50 most recent runs the Hub reads each time it refreshes a project, so the
Hub's paging and every route's growth with the project are measured. The projection job builds 30 runs in about
15 s on a GitHub runner, so both projects should take about two minutes. Each scenario but idle is sampled
`--samples` times (5 by default) at each size, and idle once per size; every sample runs in a Hub of its own. A
metric records its samples, their median and their p90 (linear interpolation between ranks). `hub_start.warm`
gathers a sample from every measured Hub.

**Before anything is measured**, one unmeasured Hub per project size opens everything once. It leaves the compiled
bytecode in the warm prefix and the project's index in that size's runtime root. Every measured Hub of that size
reuses both, as a returning user's Hub does. A worker is read only after the Hub reports the project projected and
2 s more (`--settle-seconds`), about the delay of a person's click.

**Opening Modeling** follows `ProjectWorkspace.tsx`, `useSession.ts`, `App.tsx` and `useDesignTree.ts`. The project
store's `/api/index` read, the handshake `/api/protocol` and the binding go first. Two chains then run side by side:

- **The session:** the binding, the saved position and the model list; the design history and the working copies;
  the state, beside the model's bytes when the saved position names a Stage whose model is listed (#449). Otherwise
  the bytes follow the state. Every other line's history is read once the state has answered.
- **The Design Tree:** the working source and the Worktree Graph; the head's line; every other line.

A URL already answered in the same opening is asked again with its ETag, as the browser revalidates it. The result
lists every request with its chain, status, start, time and size. Its rounds are counted on the session's chain
with the rule of `apps/monkeyhub/web/test/modelingOpen.browser.mjs`. The Design Tree and the index read run beside
that chain and are not counted, because they would merge rounds that are not shared. The client's writes are not
sent: its timing events and the viewport capture it retains after the model is on screen. The reads that come after
the model is on screen are not sent either. When the client changes how it opens Modeling, change `ModelingOpening`
with it. The browser walk stays the client's own check.

## Where results live

Each run writes one `MonkeyHubBenchmark@1` file: `commit`, `date` (UTC), `runner` (`key`, OS and version, Python,
CPU, logical CPUs), `settings`, `projects`, `metrics` (each with `unit`, `samples`, `median`, `p90`), `details`
(project-open phases, the first Modeling opening of each kind request by request, the idle window by 10 s) and
`failures` (scenario, error and the Hub's log tail). A failed sample is left out of its metric. The run continues
and exits 1 at the end.

The publish job collects both runners' files and checks `benchmark-data` out as a worktree of its checkout. If the
remote has no such branch, it starts one unborn, so the first commit has no parent. The job then:

- adds each file as `results/<runner>/<YYYY-MM-DD>-<sha12>.json`. A second run of one commit on one day keeps both
  files: the later one takes a `-2`;
- regenerates `trend.md`, the last 30 runs of every metric per runner as a sparkline with the latest median, the
  7-day median and the budget;
- regenerates `latest.json`, the newest run of each runner;
- commits as `github-actions[bot]` and pushes.

A push that loses a race fetches the branch again and adds the same files on top of it. `benchmark-data` is never
merged into `main`. A daily commit to `main` would leave every open pull request behind under the up-to-date rule.

## Budgets and flags

`tools/benchmarks/budgets.json` (`MonkeyHubBenchmarkBudgets@1`) gives each metric a budget. A budget is one number
for every runner, or `{"linux": …, "windows": …, "default": …}`. A metric is **flagged** when:

- its median is over its budget, or
- its median is more than 20% above the median of the same runner's medians over the 7 days before the run, and
  more than a floor above it as well: 200 ms for a time, CPU seconds included, and 1 MiB for bytes.

With issues on, the publish job opens one `type:perf` issue per flagged metric, titled
`perf: <metric> flagged by the daily benchmark`. The issue's table holds every runner that flagged the metric. If an
issue with that exact title is open, the job comments on it instead. Every run lists its flagged metrics in its
summary, issues on or off. A pull request run only marks a metric over its budget in its table.

**Issues are off for now.** The first budgets come from two short local runs on Windows on 2026-10-01 (an i9-13980HX
laptop that was running other work): 3 samples at 30 runs and 1 sample at 150 runs. Each budget is the larger of three
times the median and the median plus 200 ms, 0.5 CPU s or 4 MiB, rounded up to two significant figures. A metric that
both runs measured pools their samples. A GitHub runner is slower than that laptop in some ways and faster in others,
so these budgets say little about CI. Until they come from CI, `SCHEDULED_ISSUES` at the top of the workflow is
`"false"` and a dispatch's `issues` input defaults to false: runs publish and judge, and open or update no issue.
To turn issues on:

1. Let the workflow run for about 7 days.
2. Re-seed `budgets.json` from the CI medians, per runner (`{"linux": …, "windows": …}`), with the same headroom.
3. Set `SCHEDULED_ISSUES` to `"true"` in the same change.

To check the issue path without changing a file, dispatch the workflow with `issues` on and `budget_override` set to
`METRIC=VALUE`, such as `hub_start.warm=1`. That budget applies to that run's flags only; `trend.md` keeps showing the
file's budget.

## Run it locally

The harness takes built projects, because a repository tool may not import the test suite that holds the generator.
From a checkout with the repository's Python 3.12 dependencies installed (the projection job's install line):

```
cd services/project-runtime
python -c "import pathlib; from tests.synthetic_project import build_synthetic_project; print(build_synthetic_project(pathlib.Path('../../../bench-projects'), project_id='synthetic-bench-30', runs=30))"
cd ../..
python tools/benchmarks/daily_benchmark.py run --project 30=../bench-projects/synthetic-bench-30 \
    --samples 3 --idle-seconds 60 --work ../bench-work --out ../bench/result.json --summary ../bench/summary.md
python tools/benchmarks/benchmark_data.py summary --result ../bench/result.json
```

Add `--project 150=…` after building a 150-run project, which takes minutes. `--work` must be new or empty.
It keeps every Hub's log under `logs/`. On a machine where other work is running, the numbers say little: run a
short check there, and leave the trend to CI.

`python -m pytest tools/tests/test_daily_benchmark.py tools/tests/test_benchmark_data.py -q` covers the harness's own
rules (the client's opening against a fake runtime, process counters, isolation), the statistics, the flag rule, the
trend and the data branch against a temporary bare repository, and the local runner's checks, copies and publishing.

## Local runs

`python tools/benchmarks/daily_benchmark.py local` measures the installed MonkeyHub on the owner's Windows machine
with the same scenarios and metric names, and pushes the result to `results/windows-local/` on `benchmark-data`
(runner key `windows-local`, which CI never writes). Kaiwen approved local runs, a scheduled task included, on
2026-10-01 (#547).

**What runs.** The version the desktop shortcut (`MonkeyHub.lnk` on the Desktop) opens, or `--version-dir`:

- The runner keeps one copy of the installed version, `installed\<version>` in its directory (about 1 GB), and
  starts every Hub from that copy's own `_runtime\python\python.exe` with its `run.py`, as the desktop host does
  (`-u`), headless (`--no-browser --managed-stdin`, stopped with `stop` on stdin). The installed directory is only
  read.
- The first run that sees a version copies it and measures `hub_start.first_launch` once on the new copy: the first
  start of the version as shipped, with its bytecode, from files just written, as after an update. Once that run
  has published its result, `installed\<version>.json` records it, and every later run starts warm from the same
  copy and has no first-launch sample. A run that did not publish leaves the copy unrecorded, and the next run
  copies the version again. When the version changes, the earlier version's copy is renamed into the trash.
- Every Hub has its own port, runtime root, `APPDATA` and `LOCALAPPDATA` under the run directory, an account
  credential store that holds nothing, and no `PYTHON*`, `ARCHFLOW_*` or `MONKEYHUB_*` variable. A copy never sits
  right under a `versions` directory, so its update transaction stays off: it does not check for updates and never
  points the desktop shortcut anywhere. That is why the copies live in `installed\`, not `versions\`.
- The synthetic projects are built with this checkout's generator, in a process of their own as the workflow builds
  them: 30 and 150 runs (`--sizes`). Each scenario is sampled 3 times (`--samples`), idle 60 s.
- The result names the installed version's commit; `settings.installed` names the version, its release, channel,
  bundled Python, shipped bytecode and `copies` (`new` on the day of a first launch, `kept` after it),
  `settings.harnessCommit` the checkout that measured it, and `settings.local` the processor use before the run and
  what MonkeyHub ran beside it. It is published from a new repository of the run's own, never a person's checkout,
  to the checkout's `origin` (`--remote-url`), as the checkout's Git author and with the account's existing
  credentials; Git never prompts. Flags are judged against the same runner's history and only logged. A day without
  a first launch has no `hub_start.first_launch`: nothing is judged for it, and the trend leaves a gap.

**When it skips.** A skip writes one `skipped: <reason>` line to the log and exits 0:

- another local run holds `local.lock`, or a run already published today's result (its `published.json`): a run
  that failed, stopped early or was not published leaves the day open;
- the computer runs on battery, or its power source is unknown on a machine with a battery;
- nobody left the computer idle for 15 minutes within 30 minutes of waiting, or somebody used it during the wait
  (`--min-idle-minutes`, `--idle-wait-minutes`);
- the processors were more than 30% busy, averaged over 60 s (`--max-cpu`, `--cpu-seconds`). Power is checked
  again after that minute.

**An open MonkeyHub does not stop a run** (the owner's decision of 2026-10-01). The runner looks at the processes
before the run, between scenarios at most every 10 s, and after it, and keeps what it saw in
`settings.local.monkeyhub`: `desktopOpen` (the desktop app, `MonkeyHub.exe` or a process of its installation, was
open at some check), `checks`, `desktopSeen` (at how many), `desktopProcesses` (the most at one check) and
`otherHubs` (a Hub or Project Runtime from a checkout). Its summary says when the desktop app was open beside the
run. Read such a day with the app's own work in mind: an open project's workers and projections share the machine.

The runner never stops or touches a process it did not start; its own Hubs, which run from its copy, are told
apart from the desktop app's. Each Hub it starts stops with its workers when told to, or when the runner ends and
the Hub's managed stdin closes. `--force` measures without waiting and despite the checks, and keeps the reasons in
the log and in `settings.local.forcedPast`; `--no-publish` pushes nothing.

**Where things go.** Under `<development root>\temp\benchmark-local` (`python tools/dev/workspace.py paths` names the
root): `logs\local.log`, one timestamped line per start, skip, result and failure; `local.lock`; `installed\`, the
kept copy of the current version and its record; and `runs\<YYYYMMDD-HHMMSS>\` with the result, its summary, the Hub
logs under `work\logs`, the projects, the publishing repository and, once pushed, `published.json`. Nothing is
deleted: each run renames the earlier run directories, and the copies of earlier versions, into
`<development root>\_TRASH_<YYYYMMDD>\benchmark-local\` for a person to empty (about 1 GB per installed version), and
logs any run that ended without a result.

**Schedule.** `tools/benchmarks/register_local_benchmark.ps1` registers one task, `\MonkeyHub\DailyLocalBenchmark`,
for the current user, only while logged on, without elevation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1              # print it; change nothing
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1 -Register    # register or replace it
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1 -Unregister  # remove it
```

Task Scheduler starts the task when Windows declares the computer idle (an idle trigger: on Windows 8 and later,
about four minutes without input and little processor and disk use) and only on mains power. It stops the task when
the computer is used again or goes on battery, and after two hours. Windows no longer uses a task's own idle
duration, so the runner waits for the 15 idle minutes itself. The task runs `pythonw.exe`, which opens no window,
from the main checkout: `daily_benchmark.py local --dev-root <development root>`. `-Python`, `-Checkout` and
`-DevRoot` replace what the script finds; `-WhatIf` prints the task and registers nothing.

**By hand.** `local --force --sizes 30 --samples 1 --no-publish` is a short check. `run --installed <version dir>
--project 30=<dir> --out <result.json>` measures any installed version without the checks, from copies of its own
under `--work` (`--first-launch-samples` of them), as `run` measures a checkout; its runner key is then
`windows-local` unless given.

Local numbers come from one laptop that also runs other work, against CI's shared virtual machines: compare a local
run with the local trend, never with CI's `windows` runner.

## What it does not show

- A GitHub runner is a shared virtual machine. One run can be off by tens of percent, so read the trend.
- CI's first launch compiles CI Python's standard library as well, and compiles the checkout, which ships no
  bytecode. It bounds the compile cost from above. The first launch of the release package, which ships bytecode
  since #548, is measured by local runs only.
- The harness measures the server's side of opening Modeling. Parsing and drawing the model happen in a browser, which
  the harness does not run.
- The opening is replayed as the web client sends it today. The replay does not notice when the client changes; the
  browser walk and the code named above do.
- Like the projection check, it shows nothing about whether an answer is architecturally right.
