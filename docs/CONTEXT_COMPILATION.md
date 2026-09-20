# Dependency-aware context compilation

Studio prepares the model's read context and output vocabulary for the current
request. A supported numeric edit to one known element receives the requested
controls and relevant design facts. A complex design request with explicit local
targets receives a related state slice and the existing typed design output
vocabulary. The complete source state remains private for scope and validation
checks. Global requests and requests without a safely resolved area retain full
design context. All provider requests must fit the application text budget before
they can be sent. The existing deterministic compiler still makes no model call.

Development lookup and runtime requests have separate costs. Use
`python tools/devctl.py module studio.intent` to find an owner's responsibilities,
interfaces, dependencies, source paths and tests without reading the full registry.
`python tools/devctl.py module wall` searches ownership when a module id is not known.
These development commands do not affect student API requests.

## Browser tasks

The Studio sidebar groups independent tasks under the project actually bound to
the connected server. Each task retains its own transcript, draft, selection and
editing run/Stage pointer in browser-local storage. Switching tasks keeps visited
views mounted so a pending reply, candidate poll or clarification stays with its
originating task. Hidden views are inert. Rename, archive and restore change only
personal browser history; active work cannot be archived.

Task history is not sent to the intent compiler. Reload does not replay a model
request; unfinished requests and old clarification cards become history with a
resend notice. Candidate jobs with known ids can be read again. Server-held
proposal and continuation capabilities are not made durable by this sidebar.
All tasks use the connected server's configured provider and model; this is not
per-task model routing or a multi-server project service. Project persistence and
formal issue retain their existing owners. Clearing browser site data clears task
history, not project records.

## Request preparation

```text
Request + explicit selection + exact StateRecord
    -> deterministic scope classification
    -> private target, dependency closure, evidence and constraints
    -> deterministic control preflight
    -> model facts + numeric actions, or scoped/full state + design vocabulary
    -> application text budget (over limit: return unsupported without a call)
    -> provider call when within budget
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
| Design, local | A design operation names existing elements or a component, or explicitly refers to the selected element. Uses their related state slice and the complete typed design output vocabulary. | 5,000 |
| Design, full | Global work, unresolved area, gestures or document visuals. Keeps the full design context and output vocabulary, subject to the same text budget. | 5,000 |

These limits are enforced through Anthropic's `max_tokens`. Codex CLI output
allowances are advisory because this invocation interface has no equivalent
configured output cap. Neither allowance promises the model will complete an answer.

The component tier is currently limited to numeric instance edits. It does not
implement arbitrary type, reference or relationship edits. Those requests use the
design tier. An unresolved numeric field does not acquire the numeric action
shortcut; its design request can still use a local slice when the target is clear.

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

For local design requests, an exact element name identifies that element; an exact
component name identifies its authored descendant elements. A phrase such as
“this wall” must agree with the selected element. Keep clauses add read context,
not writable targets. Global scope words and unresolved areas retain the full
design path. The local slice includes declared downstream effects, upstream
references, relevant evidence and applicable or unscoped constraints. Mandatory
constraints are not shortened to meet the budget. If those facts are themselves
too large, the request is refused before inference.

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
The local design packet states `editTargets`. Its output must use `semanticEdit`;
existing entity writes stay within those targets. Locked, derived, shared and
unrelated parameter writes are checked against the private source state. New
members must declare a connection to the requested targets through references or
relationships. Sharing a broad parent or a common level is insufficient. The
existing proposal and candidate owners still check legal operations, geometry,
constraints and exact-base execution.

## Bounded context supplements

A model may return `needs_context` with one to sixteen exact existing references.
Studio validates existence and progress before adding their dependency context.
The request has at most three model calls: one initial attempt and two supplements.
Repeated, unknown, excessive or non-progressing reference requests fail. Local
design slices can expand; full design context cannot. The original writable
targets and exact checkpoint remain unchanged. Each expanded request must pass
the budget again before the next provider call. Malformed output, provider
failures and diagnostic failures do not trigger an automatic retry. Each real
attempt keeps its own usage.

## Context budget and usage

`ARCHFLOW_STUDIO_CONTEXT_BUDGET_TOKENS` configures a positive application text budget;
the default is **16,000**. `MONKEY REQUEST` preflight logs report estimates for
intent, system rules, schema, state, preferences, dependencies and application
overhead, plus the largest contributors and expected output allowance. Before
each provider call, an estimate above the limit returns the existing `unsupported`
outcome with the estimate, configured limit and an instruction to narrow the
requested area. A request exactly at the limit is permitted. Required constraints
remain intact; the rejected request is not sent and creates no model receipt or
usage. If a supplement exceeds the budget, earlier actual attempts retain their
own usage and no further call occurs.

[`intent_budget.py`](../apps/archflow-studio/api/archflow_studio_api/application/intent_budget.py)
uses the explicitly named `heuristic_utf8_bytes_div4` estimate by default and
accepts an injected text tokenizer. Runtime enforcement currently uses the default
heuristic. It counts the application's compiled rules, response schema, serialized
state, user message and known text wrappers. Estimates exclude the CLI's own
context, unknown provider framing and image-token costs. This is a hard limit on
the labeled application text estimate, not an exact billing limit or a measurement
of the provider's complete context window.

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

The benchmark includes numeric, local design and global design cases. It observes
the production budget calculation without bypassing enforcement. `compiled`
measures the prepared text even when blocked; `provider_boundary_reached` is false
for a refused request. Reaching that boundary invokes only the in-memory capture,
so `live_model_calls` remains zero for every case. `runtime_budget` reports the
actual preflight estimator separately from the counter chosen with `--tokenizer`.
`prepared_element_count`, `prepared_target_count` and `prepared_design_fact_count`
describe the model-facing packet, separately from the private source counts.

The 2026-09-10 run with the **16,000 runtime budget** measured these application
text estimates using `heuristic_utf8_bytes_div4`:

| Unrelated wall/type pairs | Scalar | Component | Local design | Global design |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 563 | 588 | 6,281 | 6,200 |
| 100 | 563 | 588 | 6,281 | 20,817 — blocked |
| 1,000 | 563 | 588 | 6,281 | 153,117 — blocked |

Scalar and component packets remain at 2,240 and 2,341 characters. The local
design packet remains at 25,103 characters and two relevant element rows, while
the private source retains the whole project. Global prepared text grows from
24,785 to 83,255 and 612,455 characters; the latter two requests never reach the
capture provider. Their required content is retained and measured, not truncated.
The sum of independently rounded runtime sections can exceed the heuristic count
of concatenated text by a few tokens.

The explicit `o200k_base` run reported that the local package/cache was unavailable;
no tokenizer data was downloaded and no new reference-token figures are claimed.
Re-run the command when schemas or rules change. These synthetic packaging
measurements establish neither billed cost savings nor model success rates.

## Retained-session versus project-state measurement (#32)

The existing manual Hub turn benchmark also measures a real provider continuing
its old session versus starting a new provider session from the same project
state. Use an explicit model returned by the installed provider's model list:

```powershell
python tests/monkeymonitor/run_turn_benchmark.py --session-pair --prepare-only --scenario incremental-edit --model <model-id> --output <absolute-nonproject-directory>
python tests/monkeymonitor/run_turn_benchmark.py --session-pair --scenario incremental-edit --model <model-id> --output <same-directory> --timeout 300
```

`--prepare-only` makes one P036 fixture archive, restores two copies through the
existing archive owner, and verifies their complete retained file manifests.
It calls no model. Running the prepared pair starts isolated Hub/Studio/Monitor
services on free ports. Each arm first performs the same read-only primer in a
real provider session; the measured request then uses `contextMode=continue` or
`contextMode=project`, with the same explicit `designContext`. The latter must
replace the provider session identity. Primer outputs are retained because the
two independently generated histories need not be textually identical.

`incremental-edit` changes the cornice height while keeping its base.
`assembly-edit` revises both heights together while preserving the footprint,
support reference, parameters and relationship. This small retained assembly
tests modification/continuation costs; it does not test building design quality.
Both require real candidate readback and the specified bounds and authored
fields. The requested source's actual `/api/state?run=...` digest is used;
the seed fixture's historical receipt digest is not substituted for it.

Use a new output directory for every repeat, and alternate
`--order continue,project` with `--order project,continue`. A used or modified
pair is refused. Only sample during a coordinated quiet window. One ordered
pair is a pilot, not evidence of a general speedup or a latency distribution.

`comparison.json` reports matching input/build/prompt/model conditions and task
success separately. Each arm retains `trace.json`, `chat.json`, `primer-chat.json`,
`input-state.json`, `candidate.json`, its P036 project under `projects/`, and
native Hub diagnostics under `runtime/`. Open that exact project directory with
the same source checkout to inspect the retained candidate. These directories
are explicit external test outputs, never a second canonical project store.

Token counts come from Monitor/provider records, with unavailable fields left
`null`. Model request count remains unknown when the provider does not expose
request identities; Agent activity intervals are reported separately. Failed
tools, retry spans and CAD builds count only observed events, and provider-internal
retries remain unknown. The benchmark reports neither account billing nor a
browser first-visible time. Different failed/partial outputs are not accepted
as faster successful work. The offline intent packaging command above remains
a separate measurement with zero live model calls.

### 2026-09-20 pilot: end-to-end behavior, not isolated rebuilding overhead

The first pairs used production `fe9bafcf` and benchmark `48234705`, the installed
Codex ACP adapter 1.11.0 and reported model `gpt-6-astra`. Each pair used identical
retained input bytes and the same measured request. The coordinator paused the
other design model/CAD work and the integration browser tests during this window;
ordinary machine/provider background variability was not controlled. All four
candidates passed the specified bounds, authored-field/support checks, unchanged
HEAD and expected provider-session continuity. These are small synthetic assembly
tasks, not evidence of architectural design quality.

| Task / condition | Wall seconds | Input | Cached input subset | Output | Tool calls | CAD builds | Failed tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cornice / continue | 35.194 | 123,488 | 115,712 | 613 | 3 | 1 | 0 |
| Cornice / project | 72.461 | 182,823 | 141,952 | 906 | 4 | 1 | 0 |
| Assembly / project | 73.070 | 276,709 | 228,864 | 1,196 | 6 | 1 | 1 |
| Assembly / continue | 53.129 | 268,347 | 254,720 | 1,272 | 5 | 1 | 1 |

Input totals already include cached input; the columns must not be added.
These provider counters are not subscription charges. Exact model-call count
and provider-internal retry count were unavailable. Recorded retry spans were
zero, but both assembly agents recovered from an invalid first proposal; these
are visible failed tool calls and subsequent corrections, not zero errors.

The cornice project-state turn made an additional model-view request (132 ms of
observed server work). The old-session turn did no visual inspection. Both also
read candidate state and their proposal after the bounded execution result.
The cornice traces attribute 31.056 s and 59.627 s to Agent activity; actual
geometry-build spans were 3 ms and 15 ms. The additional image observation and
different provider activity prevent attributing the wall-time difference to
context reconstruction alone. One pair per task cannot establish a speedup,
regression distribution, cache cause or model inference time.

Both assembly agents initially supplied only `params.height`, losing the required
profile because the current contract replaces the entire supplied `params`
object. They then resubmitted complete profiles and succeeded. The exposed
schema's broad "omitted fields retain" description did not state the nested
replacement rule already present in the compiler prompt. This repeated failure
was sent to the existing schema owner for a minimal wording correction; it is
not evidence against either `contextMode`.

All four pilot traces contain a diagnostic missing-observations notice and an
unfinished Studio mutation span; the assembly project arm additionally retains
an unfinished tool span despite the completed turn. These intervals cannot be
interpreted as completed service/tool latency, and counts above are observed
events rather than a guarantee that every diagnostic was captured. The continue arm's live report
export failed, while its model completed successfully during normal shutdown.
Its trace was reconstructed from the original Hub journal and the exact native
session; the original retained candidate was read independently and passed.
No model was rerun to replace this sample. The original child exception was not
captured, so its exact cause remains unknown. `report_recovery` labels this case.

Subsequent benchmark runs capture child stderr in `run.log`, retain failed-export
metadata, and treat Monitor's documented busy-journal HTTP 503 as a missing
snapshot rather than a failed model turn. Paired measurements now read Monitor
only after the turn, reducing competing diagnostic reads. This is a measurement
change; later repeats must name their new benchmark commit separately. The
unpaired live demonstration keeps its live observations.

Local sources are under `D:/MONKEYHUB_DEV/temp/context-bench-32/`, in
`scalar-pair-1` and `assembly-pair-1`. Each has `comparison.json` and the two
condition directories described above. Primer usage, wall time and visible
history length are separately included in each condition's `preparation`;
native provider-history token length remains unknown. Re-extract these retained
reports without a model or service:

```powershell
python tests/monkeymonitor/run_turn_benchmark.py --summarize --scenario assembly-edit --output D:/MONKEYHUB_DEV/temp/context-bench-32/assembly-pair-1
```

The primer is only one prior turn: 28.806/30.179 s for cornice continue/project,
and 29.100/29.584 s for assembly continue/project. Visible prior histories range
from 1,568 to 2,113 characters; they do not represent a long design conversation
or the complete native system/tool context. The per-arm preparation JSON holds
the corresponding input/cache/output counts, separately from the table.

### Reverse-order repeats

The second quiet window used benchmark `d3197211` with the same production
`fe9bafcf` and reported model. Monitor was read after each measured turn, not
polled throughout it. Cornice ran project then continue; assembly ran continue
then project. All four candidates passed the same independent checks.

| Task / condition | Wall seconds | Input | Cached input subset | Output | Tool calls | CAD builds | Failed tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cornice / project | 55.259 | 187,979 | 169,216 | 1,135 | 4 | 1 | 0 |
| Cornice / continue | 26.707 | 124,180 | 116,864 | 597 | 2 | 1 | 0 |
| Assembly / continue | 57.834 | 267,738 | 254,208 | 1,303 | 5 | 1 | 1 |
| Assembly / project | 99.253 | 328,232 | 276,864 | 1,820 | 8 | 1 | 1 |

The project-state cornice and assembly turns additionally requested a model
view (125 ms and 140 ms of observed server work). The assembly project turn
also reread candidate/context data. No old-session turn requested a model view.
Thus the equal candidate checks coexist with different observation work. In
these short histories project-state turns used more reported input and elapsed
time; neither observation proves context rebuilding caused the difference or
predicts a long conversation. Keep the two benchmark versions separate rather
than pooling them into a performance claim.

Both additional assembly turns repeated the missing-profile error and then
corrected it: four of four assembly turns exposed the same nested replacement
ambiguity, across both session modes and execution orders. The existing schema
owner addresses this in [PR #191](https://github.com/cogco1/MonkeyHub/pull/191);
that production change was deliberately excluded from these fixed-baseline
measurements. Its latency effect has not been measured here.

All four new reports exported normally, with no final Monitor read failure.
Each still carries a missing-observations notice and one unfinished Studio
mutation span. Removing active sampling reads did not resolve that telemetry
limitation, and the successful exports do not establish the original exception's
cause. No additional production tracing mechanism was added for this exercise.

Sources are the adjacent `scalar-pair-2` and `assembly-pair-2` directories.
Primer wall times were 28.996/25.864 s for cornice project/continue and
27.860/22.102 s for assembly continue/project; their usage and history sizes
remain separate in `preparation`. The final set is two samples per condition
per task, with one reversed order, not a statistical latency study.

## Candidate observation and revision acceptance (#183 / #32)

The existing Hub `designContext` remains a projection of the explicit editing
base. Opening a chat result or browsing a historical candidate does not change
that base. The selection request generation is invalidated when the user chooses
another semantic target, so a delayed pick cannot overwrite that later choice.
No additional design state or writer is introduced.

The opt-in experiment uses a small synthetic courtyard, the real installed
provider, the normal Hub MCP interface, OCCT and the existing Monitor journal:

```powershell
python tests/monkeymonitor/run_design_loop.py --model <installed-model-id> --output <new-absolute-nonproject-directory>
python tests/monkeymonitor/run_design_loop.py --summarize --output <same-directory>
```

It batches three masses into one candidate, lowers the east wing, sets back the
north upper mass, and explicitly studies a 4 m inward trial before correcting
its courtyard violation. This is a controlled observation task: the experiment
asks for the trial; it does not demonstrate spontaneous discovery of a bad
design. It then shuts down its owned services, starts fresh services/provider
sessions, restores the pre-movement candidate, revises the west wing, and adds
a schematic north wall from the retained massing without the earlier chat.

Successful acceptance requires exact retained sources, unchanged fixture
entities/parameters/locks/relations and all prior fields except the requested
height or translation, the retained courtyard Reading and the
north-upper support reference, successful source-bound top/front observations,
and observation of the trial before its correction proposal. Existing tool-span
timestamps must prove that the image returned before the correction started;
chat-row order alone is insufficient. Admission records
verify the candidate parent chain. Certified cold STEP solids are compared to
the requested solids, including the specified trial, by bidirectional B-rep differences and intersected with
the courtyard/access void. A regression with the same bounding box and an
incorrect notched solid fails this check. HEAD and accepted branch refs must
remain unchanged. A Reading is retained intent, not a computed constraint or
acceptance decision; the test's explicit void checks cover only this scene.

Failed provider attempts, failed tool calls and incomplete observations stay
in their original output directories. The read-only `--summarize` checker writes
`validation.json` separately from the original reports and calls no provider.
It records its own source hash and reprojects metrics from the existing usage
journal with the current Monitor code; the original reports remain unchanged.
`all_passed:false` is a failure, even if the
geometry happened to be correct. No real user project or raw provider transcript
is uploaded by this experiment. Browser first-paint time and user rework outside
the declared test requests remain unmeasured.

The mechanism follows [ReAct, ICLR 2023 camera-ready v3](https://arxiv.org/abs/2210.03629v3):
an external observation can change the next action. Its QA/game results are not
evidence of architectural design quality or an argument for an extra critic
model. The existing native image blocks match the
[MCP 2025-11-25 tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
and the actual `Image.to_image_content` implementation in the
[MCP Python SDK v1.26.0](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/src/mcp/server/fastmcp/utilities/types.py)
([MIT](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/LICENSE)).
We reuse this boundary; no screenshot-shaped JSON or new image transport is added.

For stale selection, [React's asynchronous cleanup guidance](https://react.dev/reference/react/useEffect#fetching-data-with-effects)
and [SWR v2.3.6's response-key guard](https://github.com/vercel/swr/blob/v2.3.6/src/index/use-swr.ts)
support invalidating the existing request generation. React 19.2.8 and SWR use
MIT licenses; SWR is not added as a dependency because its cache is unrelated
to this race.

Loft revisions transform existing section controls rather than replacing the
entity with another producer. Actual OCCT 7.9.3.1 cold-read tests compare against
independently transformed solids. Translation, rotation, reflection and uniform
scale preserve normal lofts; straight lofts also support nonuniform scale.
Normal loft nonuniform scale is refused: the measured refit volume ratio was
1.755844 instead of the affine determinant 1.5. The
[OCCT ThruSections parameterization contract](https://occt3d.com/dev/doc/refman/html/class_b_rep_offset_a_p_i___thru_sections.html)
and [CadQuery v2.7.0 transformShape/transformGeometry](https://github.com/CadQuery/cadquery/blob/v2.7.0/cadquery/occ_impl/shapes.py)
([Apache-2.0](https://github.com/CadQuery/cadquery/blob/v2.7.0/LICENSE)) explain why
refitting section controls is not a general affine surface transform. A new
persistent post-transform path is unnecessary for the supported cases. Bound
lofts continue through their existing parameters; direct-edit dependency guards
remain in force.

The first restart pilot exposed valid `/api/project` reads of 1.155 and 1.390 s
being classified unavailable by the worker's 1 s identity probe. The project
identity read now has a separate 5 s budget; health remains 1 s and all instance,
process, source and project checks remain required. A real managed-process test
reproduces refusal at 1 s and success at 5 s for a 1.2 s response, then confirms
that a wrong project is still refused. This is a measured engineering budget,
not an optimum. It uses the bounded-probe pattern of
[Uvicorn 0.35.0](https://github.com/encode/uvicorn/blob/0.35.0/uvicorn/supervisors/multiprocess.py)
([BSD-3-Clause](https://github.com/encode/uvicorn/blob/0.35.0/LICENSE.md)); its
automatic terminate/restart policy is not adopted.

Monitor's first successful complete readback of a candidate generated in the same
turn is now `summary.first_candidate_ms`, distinct from verification or browser visibility.
Reading an older input candidate does not trigger it; missing same-turn generation
evidence keeps it unknown.
The Hub shows it without per-run data entry. Missing child endings remain
`incomplete` with unknown duration after a root ends. The journal's bounded
write-lock correction and its source comparison are documented in
[MonkeyMonitor](../monkeymonitor/README.md#turntrace). Neither telemetry repair
nor session rebuilding establishes a general latency improvement. The Hub's
trace-comparison UI and the larger #32 benchmark set remain separate acceptance.

### Retained real-provider results, 2026-09-20

The `gpt-6-astra` six-turn run passed the final checker: seven candidate runs,
including the intentionally invalid trial, with exact final solids, original
and prior-source fields preserved, two genuinely new provider sessions for
reopen/wall, and unchanged HEAD/accepted branches. The 4 m trial intruded
215.76 m³ into the protected courtyard volume; the 2 m correction intruded
0 m³. Existing tool timestamps confirm that the trial image returned before
the correction proposal began. This verifies the declared schematic task,
not building feasibility or general autonomous design quality.

| Turn | New candidates | Tools | Failed tools | Successful model views | Turn seconds | First new candidate seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Three-wing courtyard | 1 | 9 | 2 | 1 | 171.401 | 123.258 |
| Lower east wing | 1 | 6 | 0 | 2 | 68.356 | 34.087 |
| North upper setback | 1 | 6 | 0 | 2 | 87.968 | 42.254 |
| Trial, observe, repair | 2 | 11 | 0 | 2 | 169.979 | 45.652 |
| Cold reopen, move west wing | 1 | 9 | 1 | 2 | 224.824 | 156.431 |
| New session, derive north wall | 1 | 11 | 1 | 2 | 160.874 | 110.219 |

Provider-reported token totals across the observed usage events are below;
cached input is a subset of input, not an additional charge or a unique-prompt
size. These counts do not establish model-request boundaries.

| Turn | Input tokens | Cached input tokens | Output tokens |
| --- | ---: | ---: | ---: |
| Three-wing courtyard | 388,304 | 332,928 | 2,705 |
| Lower east wing | 310,736 | 296,064 | 868 |
| North upper setback | 375,608 | 362,496 | 1,349 |
| Trial, observe, repair | 940,511 | 915,840 | 2,043 |
| Cold reopen, move west wing | 369,766 | 308,096 | 1,981 |
| New session, derive north wall | 454,535 | 387,968 | 2,275 |

The trial's first candidate is deliberately unsuitable; this metric measures
completed generation/readback, not satisfaction of the design requirement.
The four failed calls were rejected keep references or an invalid `type_ref`,
subsequently corrected by the same provider. No routine schema/read request
has been hidden from the tool counts. Model-request counts, provider-internal
retries, browser first paint and billed cost remain unknown. All six traces
have closed observed spans; absence of missing-log notices does not prove that
every possible operation was instrumented.

The local evidence root is
`D:/MONKEYHUB_DEV/temp/183-32-validation/courtyard-3`; it retains the P036 project,
original requests/reports/chat, model-derived top/front PNGs and the existing
diagnostic journal. The six-turn run used the frozen pre-review production
code; the final trace correction and stricter offline checker followed it.
Geometry, UI and worker code did not change between that run and commit
`8b7e1d05`. The final checker reprojects timings without rewriting the original
reports: the wall's old 32.993 s value was an input-candidate read, and becomes
110.219 s when same-turn generation is required.

Failures remain in adjacent directories: `baseline-simple` lacked the isolated
ACP dependency before any provider turn; `courtyard-1` lacked the user-site PDF
dependency during service setup; `courtyard-2` passed the first four spatial
steps but failed the cold-reopen observation with three
`CHAT_STUDIO_UNAVAILABLE` calls. Its old one-second worker identity timeout was
the reproduced cause. The final checker still rejects that pilot. These runs
are not discarded or counted as successful complete loops. No statistical
speedup follows from this small, uncontrolled series.

Two additional `incremental-edit --context-pack --no-preview` runs used commit
`8b7e1d05`, the same archived project bytes, prompt, `gpt-6-astra` model and
context mode, in distinct fresh provider sessions. Both changed only the
cornice height from 0.3 to 0.5 m, preserved its footprint/support and all other
authored fields, and left canonical HEAD unchanged. Both produced one CAD
build and passed readback, geometry and authored-field checks.

| Repeat | Turn seconds | First new candidate seconds | Tools | Input / cached input / output tokens |
| --- | ---: | ---: | ---: | ---: |
| 1 | 86.310 | 39.211 | 8 | 248,336 / 195,456 / 1,752 |
| 2 | 108.260 | 27.124 | 7 | 283,036 / 233,600 / 2,305 |

Both traces have zero observed failed tools and zero incomplete spans. Each
had one nonblocking Monitor read return busy/503; later reads recovered and
those poll failures remain in the reports. Browser first paint and explicit
`verified_ms` remain unknown. The variation shows why a faster first candidate
must not be presented as a faster completed turn. Evidence remains under
`D:/MONKEYHUB_DEV/temp/183-32-validation/numeric-repeats/repeat-1` and `repeat-2`.
