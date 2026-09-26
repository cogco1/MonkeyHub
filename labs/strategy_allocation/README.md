# GH-268 · State-conditioned strategy allocation (V0 lab)

> Given **one exact task state**, several competing agent strategies and a fixed
> total budget of rollouts, how should the **next rollout** be allocated as
> outcomes accumulate?

This folder is a self-contained research package for that question
([issue #268](https://github.com/cogco1/MonkeyHub/issues/268)). A collaborator
can use it without reading the rest of MonkeyHub: four finite, synthetic cases,
four strategies over one base model, an external evaluator, standardized
rollout records and an allocation interface. The algorithm-side task reduces to

> given the current per-strategy sample statistics and the remaining budget,
> return the next allocation.

Nothing here writes project state, trains a model or judges aesthetics.

## What V0 fixes

| Held fixed across arms | V0 value | Where |
| --- | --- | --- |
| Base model | the Codex CLI's own default model: `codex exec` without `-m`, user config ignored; the model the CLI reports, if any, is recorded per rollout | `strategies.CodexRunner` |
| Sampling | the CLI's default reasoning effort; `codex exec` exposes no temperature and no seed | each record's `sampling` |
| State | one exact snapshot per case, digest-pinned in [`fixtures/`](fixtures/) | `state_snapshot.py` |
| Action set | the case's full action set (4 or 5 actions) | `environment.py` |
| Token cap | 4000 output tokens per rollout, reasoning included | `rollout.run_rollout` |
| Horizon | 3 attempted actions per rollout | `Environment.rollout` |
| Timeout | 180 s per rollout call | `benchmark --timeout` |
| Environment, prompt, evaluator | `strategy-allocation-env@0`, `strategy-allocation-prompt@1`, `strategy-allocation-evaluator@0` over `strategy-allocation-reference@0` | module constants |

The allocator assigns **additional independent rollouts**. It never changes a
rollout's token cap, horizon or model. `codex exec` cannot cap output tokens,
so the harness checks the reported count after the call; an overrun is a
failed rollout (status `token_cap_exceeded`, nothing executed), exactly like a
timeout. The cap is a protocol limit, never a term of the outcome.

## Files and interfaces

| File | Holds |
| --- | --- |
| `state_snapshot.py` | `StateSnapshot`: ContextPack@1-shaped facts, exact source refs (`…#sha256=…`), a digest over everything else; immutable and self-verifying. [`state_schema.json`](state_schema.json) describes the JSON. |
| `environment.py` | `Environment`: the four cases' **public** dynamics — facts, legality, transitions, observations, previews. It judges nothing. |
| `reference.py` | The **hidden** reference: goals, per-step judgments, and the reference-optimal steps to the goal from every reachable state. Only `evaluator.py` imports it. |
| `strategies.py` | The strategy interface `Strategy` (`request` → prompt + schema, `proposal` → plan), strategies A–D, the runner interface `Runner`, `CodexRunner` and `FakeRunner`. |
| `evaluator.py` | `evaluate(env, snapshot, executed_actions, recorded_steps)` → `Outcome`; `OUTCOME_METRICS`. |
| `rollout.py` | `run_rollout(...)` → `RolloutRecord`; JSON-lines and CSV readers and writers. |
| `allocator.py` | The allocation interface `AllocationRule`: `AllocationState` → `NextAllocation`; the equal and round-robin baselines. |
| `benchmark.py` | The experiment loop and the `run`, `verify`, `summarize` and `snapshots` commands. |
| `fixtures/` | The four starting snapshots as JSON. |
| `rollout_results/` | The committed summary of the smoke run (numbers only). |

The issue's harness sketch, as implemented (`rollout.run_rollout` does all of it):

```python
env = Environment()
snapshot = env.reset(case_id)                                   # the exact state S_t
request = STRATEGIES[s].request(env, snapshot, horizon=3)       # built from the snapshot only
result = runner.run(request, seed=seed, timeout_s=180)          # one fresh, ephemeral session
proposal = STRATEGIES[s].proposal(request, result.answer)       # a plan of action ids
trajectory = env.rollout(env.state_of(snapshot), proposal.plan, horizon=3)
outcome = evaluate(env, snapshot, trajectory.executed, trajectory.steps)
```

## The four cases

Each case is a small deterministic state machine; all randomness in an
experiment is the strategy's. A step's observation and the facts it changes
are public; whether the step was good is not.

| Case | State at S_t | Actions | Known structure | Reference optimum |
| --- | --- | --- | --- | --- |
| `provided-source` | Requirement RQ r3 came with the task; its governing §4.2 is unread. The document index holds only the superseded r1 and r2. | ReadProvidedSource, BM25, RAG, Model, AskHuman | Modeling before reading the governing passage fails a precondition, even if corrected later. BM25 finds a superseded revision (wrong target). Repeated RAG adds nothing; asking the architect is a detour. | ReadProvidedSource → Model (2) |
| `protected-dependency` | Opening W1 is to be widened; it takes part in protected relation R-7, whose facts are unread. | InspectDependency, Model, Repair, RAG | Changing W1 before inspecting R-7 breaks it (precondition, forbidden dependency, hard constraint), even if repaired afterwards. Repair is illegal until a violation is reported. | InspectDependency → Model (2) |
| `open-direction` | Technical preconditions are complete; three valid roof directions remain; the template default is R1. | LocalStudy, CoarseModel, AskHuman, Inspect | Coarse-modeling before the architect chooses sets two valid directions aside for good (erroneous pruning). Asking before a comparison is a detour. | LocalStudy → AskHuman (2) |
| `local-conflict` | Accepted candidate K has one known, bounded clash. | Repair, ReModel, RAG, Inspect | The local repair is enough; inspection and unrelated retrieval are detours; regenerating the candidate changes accepted elements (wrong scope, hard constraint). | Repair (1) |

Each case has exactly one optimal first action, at least one harmful one and
some detours, and no single fixed procedure is optimal in all four: that is
what makes the state matter. Do not give a strategy access to this folder.

## The four strategies

All four use one base model, **one prompt contract** and **one output schema**
(`{"plan": [...], "ranking": [...], "rationale": "..."}`), the same horizon and
the same token cap. They differ only in policy, context and tool exposure:

| Id | Policy | Sees, beyond the snapshot and action set |
| --- | --- | --- |
| A | free choice | nothing |
| B | fixed procedure | the same GATHER → PRODUCE → CHECK procedure for every state; the model only maps steps to actions |
| C | state-ranked actions | the environment's compiled legal actions for the current state; it must rank them, and its plan must start with its top-ranked action (otherwise `policy_violation`) |
| D | short lookahead | a public one-step preview of every action: the observation and the facts that would change, or the refusal |

Every rollout is one fresh `codex exec --json --ephemeral --ignore-user-config
-s read-only --output-schema …` process in an empty temporary directory. The
prompt goes in as exact UTF-8 bytes on a stdin pipe the runner owns and closes;
no session is resumed and no transcript is inherited. A strategy never sees
another rollout: the only thing that crosses between rollouts is the
allocator's aggregate statistics. Each record keeps the provider's session id
and the SHA-256 of the exact prompt, so the independence and the "same state
for every arm" claims can be checked from the data.

## Outcome and resources are separate

`evaluate` replays the executed actions from the snapshot (a trajectory is
read back, not trusted) and returns the **external outcome**:
`completed`, `preconditions_satisfied`, `hard_constraints_kept`, `right_target`,
`no_forbidden_dependency`, `no_erroneous_pruning`, `illegal_actions`,
`steps_executed`, `reference_optimal_steps`, `excess_steps` (steps wasted
against the reference optimum, assuming an optimal continuation; null when the
goal can no longer be reached), `first_action_class` (`optimal`, `detour`,
`harmful`, `illegal`), `success` (completed and no flag) and `failure_reasons`.
It takes no tokens, time or cost as input and has no field for them.

Resources sit beside it in each record's `resources`: tokens (Monitor's
convention: input includes cached input, output includes reasoning), wall
seconds, provider calls, provider tool calls, environment actions, human
interruptions (executed AskHuman actions) and API cost (null: `codex exec`
reports none).

The allocator reads **one scalar per rollout**, computed from the outcome
alone: `success` by default, or `first_action_optimal`. A rollout that yields
no executable proposal — timeout, malformed answer, policy violation, token-cap
overrun or provider error — is retained, scores 0 and is never retried or
replaced. (In `labs/candidate_evaluation` a failed evaluation is charged but not
observed; here the failure is the strategy's own result.) Its `status` column
lets an analysis set infrastructure errors apart.

## The allocation interface

```python
class AllocationRule(Protocol):
    name: str
    def next_allocation(self, state: AllocationState) -> NextAllocation: ...

AllocationState(strategies=(StrategyStatistics(strategy_id, n, sample_mean, sample_variance), ...),
                remaining_budget, fixed_sample_cost=1.0, parallel_capacity=1,
                attempts_so_far=0, round_index=0, warmup=2, seed=0)
NextAllocation(allocations=(("C", 3), ("A", 1)), stopping_reason=None, diagnostics=(...))
```

`n` counts every attempted rollout of the strategy at this state; the variance
is the unbiased sample variance (none for one sample). The two V0 baselines,
`equal` and `round_robin`, delegate each single decision to
`SequentialAllocator` from `labs/candidate_evaluation`, unchanged. Their warmup
is the balanced initialization (two rollouts per strategy by default), so
allocation is sequential from the first round. Because every attempt is an
observation here, the two baselines allocate the same counts; they are the
reference for the first adaptive rule, not a comparison in themselves. This
slice has **no OCBA-style rule**.

To add a rule: implement `AllocationRule`, register it in `allocator.RULES`,
test it next to `test_allocator.py`, then run it offline with
`benchmark run --runner fake --rule <name>`. `SequentialBaseline` fills a batch
by counting scheduled rollouts as observed with placeholder moments; that is
right only for count-based rules, so a rule that reads means or variances must
batch on its own terms.

## Running it

Run everything from the repository root with the root on `PYTHONPATH`
(Python 3.12; `jsonschema` is needed only by the schema test).

```sh
python -m pytest -q -p no:cacheprovider labs/strategy_allocation      # 59 offline tests, a few seconds
python -m labs.strategy_allocation.benchmark snapshots --check         # fixtures are today's snapshots
python -m labs.strategy_allocation.benchmark run --runner fake --out <new-directory>
python -m labs.strategy_allocation.benchmark verify <run-directory>
python -m labs.strategy_allocation.benchmark summarize <run-directory>
```

The fake runner's behaviours (`strategies.DEMO_PLANS`) are invented so the arms
differ; they measure nothing. Tests never call a model; CI does not run lab
tests. `--out` must be a new or empty directory outside the repository; results
are never overwritten or silently resumed. `verify` cold-reads a run: it
re-derives each snapshot, replays and re-judges every rollout, replays every
allocation decision from the outcomes retained before it and recomputes the
summary.

A run directory holds `config.json` (the fixed conditions, runner description,
versions and snapshot digests), `snapshots/`, `rollouts.jsonl` (one record per
rollout, appended as it completes), `rollouts.csv` (the flat view),
`allocation.jsonl` (each round's statistics, allocation, shuffled run order and
stopping reason), `summary.json` and, with `--raw`, `raw/<rollout>/` holding the
exact prompt, schema, CLI events, stderr and answer of each call. The records
carry the issue's fields: case, snapshot id and digest, strategy id and
version, provider, version and model, sampling, seed, token cap, horizon,
chosen actions, trajectory, evaluator result, success and failure reason, and —
as resources only — tokens, wall time and tool calls.

Real runs need an authenticated Codex CLI; the harness handles no credential.
`--max-provider-calls` (default 16) refuses a Codex run that could make more
calls. `--low-priority` runs the process and its children at idle priority.

## To add a strategy or a case

- **Strategy**: add a `PromptStrategy` to `strategies.STRATEGIES` with a new id
  and version, or implement `Strategy` yourself; keep the shared schema so
  outcomes stay comparable. A strategy gets the snapshot and the public
  environment, never the evaluator or the reference (`test_isolation.py`
  enforces the imports and scans every prompt). A scripted, model-free
  strategy is a `Runner` that answers without a model, as `FakeRunner` does.
- **Case**: add a `Case` in `environment.py` (actions, initial state, sources,
  facts, refusals, effects) and its `CaseReference` in `reference.py` (goal and
  step judgment); regenerate the fixtures with
  `benchmark snapshots --write labs/strategy_allocation/fixtures`; add evaluator
  tests that pin its known structure.

## Smoke run (2026-09-26)

Sixteen real calls, the whole allowance: two cases × four strategies × two
rollouts, equal allocation (budget 8 per case, warmup 2, four per round in a
seeded shuffled order), seed 268, `codex-cli 0.153.4` with its default model
and effort, horizon 3, cap 4000 output tokens, idle priority. The summary is
in [`rollout_results/smoke-2026-09-26/`](rollout_results/smoke-2026-09-26/);
prompts, CLI events and answers stayed on the owner's machine.

- All 16 calls returned status `ok` in 16 distinct Codex threads, with no
  provider tool call and no cap overrun. `verify` reproduced the run from its
  snapshots, including every allocation decision.
- Per call: 18,499–19,345 input tokens (mean 18,794; almost all of it the
  CLI's own harness prompt, cached for only one call), 58–173 output tokens
  (mean 91, reasoning 0–104) and 5.4–9.5 s (mean 6.9 s).
- `provided-source`: all eight rollouts chose ReadProvidedSource → Model, the
  reference optimum. C ranked AskHuman second and BM25 below it.
- `open-direction`: A, C and D proposed LocalStudy alone, twice each — the
  optimal first action, no pruning, no wasted step. B's fixed procedure mapped
  PRODUCE to CoarseModel both times and pruned two valid directions. Under
  reference@0 none of the 8 completed, because its goal also required the
  architect's choice while the task text says "one step forward".
- The CLI's JSON events name no model, so records say `cli-default`; pin
  `--model` for a full run.

These are 2 samples per arm: they show the harness working, not a ranking.

## What is not claimed

- Nothing about which strategy or which allocation rule is better. The smoke
  run shows that the harness works on real calls; its samples are far too few
  for a comparison.
- No OCBA-style rule, and no OCBA guarantee. Independence, normality and a
  unique best are unchecked for LLM rollouts; Bernoulli outcomes at small n
  often have zero sample variance.
- The snapshot has ContextPack@1's shape, but in this slice it comes from the
  synthetic environment, not from `POST /api/intents/context`.
- The token cap is checked after the call, not enforced inside it. Codex's
  server-side prompt cache (`cached_input_tokens`) lowers cost; it carries no
  conversation between sessions.
- The read-only sandbox forbids writes, not reads. The prompt forbids commands
  and the working directory is empty; `provider_tool_calls` counts any command
  the CLI ran anyway, and such a rollout should be inspected before use.
- The rollout seed drives only the fake runner and the run order; Codex
  sampling is not seedable.
- Non-goals as in the issue: training or fine-tuning, a general reward model,
  aesthetic evaluation, changing canonical state, conversation memory as
  experiment state, a dynamic token cap in V0.

## Open decisions for the collaborator

1. The allocation outcome: `success` (default), `first_action_optimal`, or a
   graded one such as negative `excess_steps`.
2. Whether provider errors keep scoring 0 (the issue's "all failures count") or
   are excluded in analysis; both are possible from the retained records.
3. Whether B should stay a model-executed fixed procedure or become a
   model-free scripted baseline (a deterministic arm, sampled once).
4. The first adaptive rule, and how it handles zero-variance Bernoulli arms at
   small n; only then an equal-vs-adaptive comparison at matched budget.
5. Whether the case set needs stochastic transitions, harder traps or more
   states per case before a full run.
