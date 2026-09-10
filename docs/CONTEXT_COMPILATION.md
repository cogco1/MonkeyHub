# Dependency-aware context compilation

Studio prepares the model's read context and output vocabulary for the current
request. A supported numeric edit to one known element receives the target,
required dependencies, applicable readings and constraints. Broad or uncertain
requests retain the complete design context. The existing deterministic compiler
still makes no model call.

Development lookup and runtime requests have separate costs. Use
`python tools/devctl.py module studio.intent` to find an owner's responsibilities,
interfaces, dependencies, source paths and tests without reading the full registry.
`python tools/devctl.py module wall` searches ownership when a module id is not known.
These development commands do not affect student API requests.

## Request preparation

```text
Request + explicit selection + exact StateRecord
    -> deterministic scope classification
    -> scalar, component or design answer schema
    -> target, dependency closure, applicable evidence and constraints
    -> provider call
    -> request-output validation
    -> existing proposal and candidate validation
```

The implementation extends `studio.intent` in
[`intent_context.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_context.py),
[`intent_requests.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_requests.py)
and [`intent_agent.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_agent.py).
There is no model router or second project store.

| Tier | Current trigger and output | Anthropic output limit |
| --- | --- | ---: |
| Scalar | One recognized numeric field on one exact selected or named element. Uses the existing scalar grammar and a small answer schema; semantic edits are disabled. | 800 |
| Component | Several existing numeric fields on one element with an advertised producer signature. Permits that element and its requested existing parameter bindings. | 2,400 |
| Design | Uncertain scope, unsupported numeric control, multiple targets, creation/removal, references/types, broad architectural work, gestures or document visuals. Keeps the full design context and output vocabulary. | 5,000 |

These limits are enforced through Anthropic's `max_tokens`. Codex CLI output
allowances are advisory because this invocation interface has no equivalent
configured output cap. Neither allowance promises the model will complete an answer.

The component tier is currently limited to numeric instance edits. It does not
implement arbitrary type, reference or relationship edits with reduced context.
Those requests use the design tier. An ambiguous request such as “change this
window to 1200” also retains full context because the field is unresolved.

Scope follows the record's declared dependency edges and affected closure.
Read context also retains parents, types, hosts, levels, grid-role lookups,
parameter expression inputs, applicable relations and their validators, explicit
keep references and obligation blockers. Unscoped readings and obligations, and
declared parameter locks, remain visible. This does not establish that the
project's declared architectural dependencies are complete.

Output schemas follow the same scope. They restrict writable targets and fields,
retain the producer vocabulary needed by component edits, and share repeated
definitions through JSON Schema references. Producer signatures are removed from
the state packet once the response schema supplies that vocabulary. Extra read
context never authorizes changes to another element or shared type.

## Bounded context supplements

A model may return `needs_context` with one to sixteen exact existing references.
Studio validates existence and progress before adding their dependency context.
The request has at most three model calls: one initial attempt and two supplements.
Repeated, unknown, excessive or non-progressing reference requests fail; full
design context cannot expand. Malformed output, provider failures and diagnostic
failures do not trigger an automatic retry. Each real attempt keeps its own usage.

## Context budget and usage

`ARCHFLOW_STUDIO_CONTEXT_BUDGET_TOKENS` configures a positive advisory input budget;
the default is **16,000**. `MONKEY REQUEST` preflight logs report estimates for
intent, system rules, schema, state, preferences, dependencies and application
overhead, plus the largest contributors and expected output allowance. Going over
budget emits a warning. It does not truncate constraints, block the call, choose a
cheaper model or retry.

[`intent_budget.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_budget.py)
uses the explicitly named `heuristic_utf8_bytes_div4` estimate by default and
accepts an injected text tokenizer. Estimates exclude unknown provider framing
and image-token costs. They are neither exact billing counters nor a model's
complete context-window measurement.

With the existing `MONKEYMONITOR_DATA_DIR` diagnostics enabled, each model-request
event retains:

- Provider-reported `tokens.input_tokens`, `cached_input_tokens`, `output_tokens`
  and available cache-write/reasoning subsets. Missing counters remain `null`.
- The actual reported `model`, plus `details.task_type`, `success`,
  `validator_pass`, `validator_scope`, `escalation`, `retry_reason` and `retry_attempt`.
- `details.context_budget.section_tokens`, `estimated_input_tokens`, `estimator`,
  `budget_tokens`, `exceeded`, `expected_max_output_tokens`, image-count metadata
  and at most five `largest_contributors`.

`validator_scope` is `request_output`: the answer satisfied the request's output
contract and scope. Architectural or geometric acceptance remains with the
existing proposal/candidate checks. A successful network response is not itself
a compiled design change. Estimated sections are never copied into actual usage
or pricing fields. Diagnostic records contain bounded metadata and counts, not
prompts, source text, provider responses or credentials.

## Reproduce the offline comparison

From the repository root, using a Python environment with the Studio dependencies:

```powershell
python tools/benchmark_intent_context.py --siblings 0 100 1000 --tokenizer heuristic
```

For a locally installed `tiktoken` with an existing `o200k_base` cache:

```powershell
python tools/benchmark_intent_context.py --siblings 0 100 1000 --tokenizer o200k_base
```

`--tokenizer auto` uses that local encoding when available, otherwise the named
byte heuristic. The command never downloads tokenizer data, calls a model,
runs geometry or writes a report file. JSON goes to stdout. `o200k_base` is a
repeatable local counter, not an exact Anthropic or Codex billing tokenizer.
If the cache is outside tiktoken's default location, set `TIKTOKEN_CACHE_DIR`
to the existing cache directory before running the command.

The fixture constructs real `StateRecord` and `StateProjection` values with a
selected wall, dependent parapet, level, grid, type, parameter and two obligations.
It adds unrelated wall/type pairs. The benchmark executes the production
preparation loop and substitutes only the provider call with a capture. It compares
the old full `SYSTEM_PROMPT + response_schema + record_sheet` packaging against
the actual prepared rules, schema and state, serialized as Anthropic text inputs.
Section counts are independently tokenized diagnostics and may not sum to the
token count of concatenated request text.

The initial fixture comparison measured the following **characters**, including
rules, schema, request text and application wrappers:

| Unrelated wall/type pairs | Scalar: full → compiled | Component: full → compiled | Design: full → compiled |
| ---: | ---: | ---: | ---: |
| 0 | 49,492 → 6,364 | 49,510 → 20,382 | 49,546 → 24,883 |
| 100 | 105,762 → 6,364 | 105,780 → 20,382 | 105,816 → 83,353 |
| 1,000 | 615,162 → 6,364 | 615,180 → 20,382 | 615,216 → 612,553 |

The same prepared text measured with **`tiktoken:o200k_base` reference tokens**:

| Unrelated wall/type pairs | Scalar: full → compiled | Component: full → compiled | Design: full → compiled |
| ---: | ---: | ---: | ---: |
| 0 | 13,822 → 1,805 | 13,829 → 5,417 | 13,825 → 6,819 |
| 100 | 32,723 → 1,805 | 32,730 → 5,417 | 32,726 → 26,520 |
| 1,000 | 202,823 → 1,805 | 202,830 → 5,417 | 202,826 → 203,820 |

The narrow requests retain the same two relevant elements, one type and both
obligations as unrelated siblings grow. The design request retains all elements
and types, so its state still grows with the project. The compiled full context
also preserves authored fields and obligations omitted from the previous sheet.
At 1,000 siblings that additional context outweighs schema savings by 994 reference
tokens, despite slightly fewer characters. Broad requests therefore have no
guaranteed token reduction. Re-run the command when schemas or rules change.
These synthetic packaging measurements establish neither billed cost savings nor
model success rates.
