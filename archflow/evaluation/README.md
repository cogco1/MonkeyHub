# Evaluation

Owns read-only critics and soft multi-objective evaluators. Results should
remain structured as evidence, score vectors, uncertainty, and trade-offs rather
than being collapsed prematurely into a single mandatory taste metric.

Evaluation cannot waive a hard-constraint failure and cannot mutate state. See
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

Current status: P1 structured metric and `EvaluationObservation` contracts plus
a neutral claim-coverage evaluator. Soft evaluator exceptions produce
read-only error observations and do not gain commit authority.

## Aesthetic observations

`aesthetic.py` adds an evidence-grounded, multi-objective observation boundary
for proportion, massing coherence, facade rhythm, legibility, spatial variety,
material coherence, and view-dependent quality.

The evaluator receives only a detached `AestheticSnapshot`: an exact
`StateRef`, a candidate ID, and immutable references to rendered views. It has
no canonical-state writer, committer, MCP adapter, hard-validator receipt, or
world handle. Missing views and evaluator failures produce unavailable/error
observations with no invented metrics.

Scores remain a vector with per-objective rationale, confidence, and view
references. `pareto_front()` can expose non-dominated trade-offs, but it has no
weights, aggregate score, automatic winner, or promotion action. Hard usability
validation remains a separate prerequisite that aesthetic output cannot waive.

## Reproducible experiments

`experiment.py` adds a read-only empirical protocol above existing source
authorities. `ExperimentPreregistration@1` freezes separate project cases, the
complete case–condition matrix, one shared provider profile, wall-clock and
attempt bounds, metric specifications, stopping rules, and code/contract
identity before execution. It contains no outcome field and cannot invoke a
provider or write a project.

`ExperimentAttemptIntent@1` is the pre-execution boundary. A persisted intent
means an assignment started; an absent intent remains planned. Terminal and
failed `ExperimentAttemptReceipt@1` values remain distinct, bind exact provider
and project evidence, prohibit fallback, and require an exact predecessor for
retry. Validation ablations reuse an exact terminal source attempt and cannot
replay the provider. Generation-context ablations declare the named withheld
context but cannot mutate or replace the production route.

Each condition preregisters its exact terminal authority requirements: role,
source schema, and allowed source status. A completed full or generation
attempt must bind the complete evidence set named by that study; a validation
ablation may remove only its named evaluator and must reuse every unchanged
source binding byte-for-byte.

The persistence adapter parses known P056, sandbox, P060, and P061 receipts
and then checks their geometry, design-state, component-tree, scene, sandbox,
family-set, and family-compilation digests as one same-project chain. A family
compilation with no instance is a no-op and cannot satisfy a completed P062
assignment.

`ExperimentOutcome@1` reports every preregistered metric as measured, unknown,
or not applicable. Required unknowns make an outcome ineligible for comparison;
they are never imputed. Source validation status is preserved as detached
evidence, not upgraded by evaluation. No experiment record can waive a hard
failure, select a winner, promote state, or acquire P036/P053 authority.

`ExperimentResultIndex@1` is derived only from exact P036 study-record
bindings. It keeps planned, running, terminal-but-unmeasured, failed, and
completed assignments distinct; a completed provider attempt cannot be
retried. Its paper-table readiness flag remains false while any assignment is
unrun, running, or lacks an outcome, and the index cannot include a manually
asserted result.

`tools/run_experiment.py` exposes the corresponding P036 boundaries as
`preregister`, `intent`, `receipt`, `outcome`, `index`, and `verify` commands.
Preregistration, receipt, and outcome ingestion require explicit
`PROJECT_ID=ROOT` case repositories. Preregistration refuses a missing case,
wrong base, changed raw-request digest, study-as-case alias, or unretained input
reference before writing the protocol. Later commands re-read every detached
source before accepting the study record. The tool never invokes a provider:
`archflow-runtime run-project` remains the P053 production entry, so an
experiment intent must be durable before that separate execution begins.

The currently supported generation ablation is named
`program-relationship-context`. The P062 adapter derives a second valid
`ProductionAuthoringContext@1` from the exact full context by removing only its
typed program relationships and rebinding the build-policy program digest. It
persists both the ablated context and an
`ExperimentGenerationContextAblationReceipt@1`; the receipt freezes removed
relationship identities and states that the P053/P056 route and provider
profile remain unchanged. The full context is never edited.
Before a provider assignment can start, the adapter selects the exact retained
context by condition: full uses the cited full-context record, generation uses
the ablation receipt's cited derived record, and validation ablation is rejected
as a provider invocation. This avoids ambiguous auto-loading when both contexts
exist in one case run.
