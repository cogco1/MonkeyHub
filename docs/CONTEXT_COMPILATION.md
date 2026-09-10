# Dependency-aware context compilation

Studio prepares the model's read context and output vocabulary for the current
request. A supported numeric edit to one known element receives the requested
controls and relevant design facts. The complete execution and validation closure
stays on the server. Broad or uncertain
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
    -> private target, dependency closure, evidence and constraints
    -> deterministic control preflight
    -> model facts + numeric actions, or full design vocabulary
    -> provider call
    -> request-output validation and server action adaptation
    -> existing proposal and candidate validation
```

The implementation extends `studio.intent` in
[`intent_context.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_context.py),
[`intent_requests.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_requests.py)
and [`intent_agent.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_agent.py).
There is no model router or second project store.

| Tier | Current trigger and output | Anthropic output limit |
| --- | --- | ---: |
| Scalar | One recognized numeric field on one exact selected or named element. The model proposes one numeric action; the server supplies target identity and the existing scalar grammar. | 400 |
| Component | Several existing numeric fields on one element with an advertised producer signature. The model proposes one action per requested field; the server preserves untouched record data. | 800 |
| Design | Uncertain scope, unsupported numeric control, multiple targets, creation/removal, references/types, broad architectural work, gestures or document visuals. Keeps the full design context and output vocabulary. | 5,000 |

These limits are enforced through Anthropic's `max_tokens`. Codex CLI output
allowances are advisory because this invocation interface has no equivalent
configured output cap. Neither allowance promises the model will complete an answer.

The component tier is currently limited to numeric instance edits. It does not
implement arbitrary type, reference or relationship edits with reduced context.
Those requests use the design tier. An ambiguous request such as “change this
window to 1200” also retains full context because the field is unresolved.

Private scope follows the record's declared dependency edges and affected closure.
The internal context retains parents, types, hosts, levels, grid-role lookups,
parameter expression inputs, applicable relations and their validators, explicit
keep references and obligation blockers. Unscoped readings and obligations, and
declared parameter locks, remain visible. This does not establish that the
project's declared architectural dependencies are complete. These records are not
automatically copied into a numeric model request. The model sees requested fields,
current values, verified units, relevant design requirements and concise shared
effects. Unrelated locks, raw expressions, source bindings, producer contracts and
execution bookkeeping remain private. Only explicitly requested supplements expose
additional dependency dimensions.

Both numeric tiers accept a small action envelope. An action can be:

```json
{"op":"set_parameter","field":"thickness","value":300,"unit":"mm"}
```

The target is already bound on the server. The model does not return project or
element ids, internal grammar, complete Entity/Parameter records, or unchanged
metadata. The server checks the exact requested field set, converts declared units,
and adapts actions into the existing proposal inputs. Unchanged params, references,
types, source evidence and parameter knowledge status survive by construction.
Missing unit evidence never authorizes a guessed conversion. Percent operations
are also explicit actions, with arithmetic performed by the server.

Shared or derived controls require a design choice; locked controls are refused.
Known blockers are handled before inference and create no model receipt or usage.
The server does not silently change other consumers or detach a binding. Clear,
single-field absolute wall height/thickness requests with explicit metric units
also use the existing deterministic grammar path. Extra clauses, uncertain targets,
unknown units and unsupported controls do not acquire this shortcut.

The design tier retains the complete producer vocabulary, with repeated schemas
shared through JSON Schema references. Producer signatures are removed from the
state packet once the response schema supplies that vocabulary. Additional read
facts never authorize another writable target or shared type.

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
the actual prepared rules, schema and `model_context` state, serialized as
Anthropic text inputs. The private validation sheet is not counted as sent text.
Section counts are independently tokenized diagnostics and may not sum to the
token count of concatenated request text.

The fixture comparison measured the following **characters**, including
rules, schema, request text and application wrappers:

| Unrelated wall/type pairs | Scalar: full → compiled | Component: full → compiled | Design: full → compiled |
| ---: | ---: | ---: | ---: |
| 0 | 49,492 → 2,240 | 49,510 → 2,341 | 49,546 → 24,785 |
| 100 | 105,762 → 2,240 | 105,780 → 2,341 | 105,816 → 83,255 |
| 1,000 | 615,162 → 2,240 | 615,180 → 2,341 | 615,216 → 612,455 |

The same prepared text measured with **`tiktoken:o200k_base` reference tokens**:

| Unrelated wall/type pairs | Scalar: full → compiled | Component: full → compiled | Design: full → compiled |
| ---: | ---: | ---: | ---: |
| 0 | 13,822 → 603 | 13,829 → 639 | 13,825 → 6,791 |
| 100 | 32,723 → 603 | 32,730 → 639 | 32,726 → 26,492 |
| 1,000 | 202,823 → 603 | 202,830 → 639 | 202,826 → 203,792 |

For narrow requests, the model receives one target and five design facts; it
receives no full element or type reconstruction rows. The private validation
context still retains two relevant elements and both obligations as unrelated
siblings grow. The benchmark reports `sent_target_count` and `sent_design_fact_count`
separately from `private_element_count` and `private_obligation_count` so these
are not mistaken for the same packet. Scalar schema/state text measures 275/195
reference tokens; component schema/state text measures 278/221.

The design request retains all elements and types, so its state still grows with
the project. The compiled full context also preserves authored fields and
obligations omitted from the previous sheet.
At 1,000 siblings that additional context outweighs schema savings by 966 reference
tokens, despite slightly fewer characters. Broad requests therefore have no
guaranteed token reduction. Re-run the command when schemas or rules change.
These synthetic packaging measurements establish neither billed cost savings nor
model success rates.
