# GH-173 synchronous checkpoint experiment

One finite, synthetic courtyard housing fixture compares **review timing × role
context**, without changing MonkeyHub's production runtime or canonical state.

| Arm | Review context | Time |
| --- | --- | --- |
| A | Proposer's public conversation, self-review | After entry, gallery and unit access |
| B | Same self-review context | After each of those three decisions |
| C | Separate Critic context, same model and exact proposal bundle | After all three decisions |
| D | Same separate Critic context | After each decision |

Each invocation is a fresh Claude CLI process. A/B self-review receives the
explicit public proposer transcript; C/D reviews receive the same frozen design,
source/delta/candidate references, public proposal assumptions, policy and checks,
without the proposer's conversation. This tests context separation, not independent
model knowledge. All construction and repair calls retain the public conversation.

## Fixed protocol

The predeclared live pilot has **three repetitions per arm**, 12 scheduled trials.
Repetition orders are ABCD, BCDA and CDAB. Three independent repetition blocks may
run concurrently; stages within a trial are synchronous. CLI seed control is
unavailable; repetition numbers are not seeds. The fixed primary model is
`claude-opus-5`, effort `low`, Claude CLI 2.1.272. Actual primary and auxiliary
model identities are recorded per call rather than inferred from that setting.

Each arm has the same total ceiling: **10 CLI calls, 900 seconds, $3 API-equivalent
cost**, including proposal, all reviews, repairs and rejected attempts. Each call
has at most 180 seconds, two CLI agentic turns, native API retry limit zero and
one structured-output attempt. The requested output cap is 4096 tokens; the CLI
does not prove that cap for every auxiliary request. Thus this is a cost/call/time
ceiling comparison, not a claim of exact total-token matching. All per-model input,
cache and output usage is reported; cache is a subset of inclusive input. Missing
telemetry stops the trial and stays unknown. API-equivalent cost is **not an account
bill or subscription quota measurement**. Exact API request counts are unknown.

Each review can cause one repair. Construction has at most two attempts per
checkpoint, and there are no speculative branches or asynchronous shadow critics.
Checks execute in bounded batches at design decisions. Immediate core exact-base,
parameter-lock and protected-courtyard checks apply in every arm. A reviewer can
request the same finite independent goal checks; only failed applicable checks
justify revision or blocking. Disagreement itself cannot veto a candidate. Final
assessment runs identically for every arm and is not fed back to trigger another
repair. Malformed, stale, timeout, exhausted, omitted and failed trials remain in
the denominator, including a valid design whose required review never completed.

## Fixture and what it measures

The same 12 × 12 m, two-storey schematic ring has a protected 4 × 4 m courtyard:
128 m² footprint, 256 m² gross floor area, two floors, 6 m height. Existing
`StateRecord` operators create the candidate; the existing #123 `MassingEvaluator`
checks its retained inclusive-cell projection. No #123 owner file is modified.

The street gate is west. An east entry is a labeled outstanding completion
obligation, not an invariant violation. Gallery and unit access can remain absent
before completion. Generic/unknown semantics remain valid even at schematic
completion. Structural analysis remains unavailable because loads, materials and
supports are missing. Courtyard width is locked; an attempted reduction is a real
core invariant refusal.

The actual declared parameter graph is `entry_side → gallery_x → door_x →
threshold_x`. The core recomputes existing dependents when the entry changes.
Depth and downstream count come from that graph; repair scope counts existing
values actually changed. A late repair affects two earlier decision operations
and three dependent values. These coordinates are schematic decision anchors:
**they do not demonstrate a navigable CAD route, clearance, code compliance,
structural safety or architectural quality**. The massing measurement is not a
substitute for any of those judgments.

Scripted controls deliberately introduce an east-entry error to test the timing
measurement. They are separate from live trials, whose choices come from real
provider responses. Missed errors and aborted error-bearing trials have null
propagation depth plus an observed lower bound; they are never zero-propagation
successes. No-origin cases have no error denominator. Content diversity uses the
existing design content digest, not the run-bound file digest.

## Ownership and storage

Implementation and tests stay here under `labs/`. Registered production owners
are consumed without edits: `state.record`, `state.massing_metrics`, P036 project
repository, Hub CLI discovery/process cleanup and MonkeyMonitor `TokenUsage`.
The #123 lab is read-only. There is no general agent framework or new record kind.

The explicitly promoted synthetic evidence root is
[`probes/checkpoint-critique`](../../probes/checkpoint-critique/). Every candidate
is an existing `state-record` in a named P036 run. Public requests, structured
answers, checks, decisions, usage and reports are immutable JSON artifacts written
through `put_workspace_file`. The project begins with an empty canonical envelope;
the authored fixture and all revisions are candidates. HEAD and design branches
must remain unchanged. No accepted Stage or project version is issued.

Only this public synthetic fixture is sent to the provider. The child process uses
an empty temporary working directory, safe mode, no tools, no MCP, no browser,
no native session persistence and no project writer. Native stdout/stderr,
account details and private reasoning are not saved. Provider output is restricted
to public JSON decisions and brief evidence summaries.

## Run and verify

```powershell
python -m unittest labs.checkpoint_critique.test_fixture labs.checkpoint_critique.test_provider labs.checkpoint_critique.test_harness
python tools/archcheck.py
python -m labs.checkpoint_critique.benchmark --project probes/checkpoint-critique --batch YOUR-UNIQUE-CONTROL --repeats 1
python -m labs.checkpoint_critique.benchmark --project probes/checkpoint-critique --batch YOUR-UNIQUE-LIVE --real --repeats 3 --parallel-blocks 3
```

`--real` makes actual provider calls. Use a unique batch name: retained data is
immutable and a prior batch must not be overwritten or silently resumed. To use
an external project root, supply an explicit directory named `checkpoint-critique`.
The batch plan is persisted before the first scheduled model call. All scheduled
trial outcomes and their references are summarized. A trial-level infrastructure
exception keeps an unresolved row and its partial run; if storage itself fails,
the immutable plan identifies missing slots instead of authorizing their removal.

`harness.replay(repository, report_ref)` cold-reads the exact retained byte refs,
checks the source/candidate/delta chain, reapplies existing operators, replays
review adjudication and compares the final independent result. It verifies call
artifact integrity; it does not recreate stochastic model answers or independently
audit the provider's billing telemetry.

Research papers, pinned implementations, licenses and adoption/rejection choices
are in [RESEARCH.md](RESEARCH.md). Measured outcomes and remaining limits will be
reported in [RESULTS.md](RESULTS.md) after the fixed live pilot.
