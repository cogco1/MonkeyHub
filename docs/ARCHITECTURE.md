# ArchFlow V4 Architecture

The current implementation coverage and work-card map live in
[DYNAMIC_MAP.md](DYNAMIC_MAP.md).

![ArchFlow V4 phase-gated dual-state architecture](diagrams/v4-bounded-agency.svg)

Diagram variants:

- Chinese: [SVG](diagrams/v4-bounded-agency.svg) ·
  [PNG](diagrams/v4-bounded-agency.png)
- English: [SVG](diagrams/v4-bounded-agency.en.svg) ·
  [PNG](diagrams/v4-bounded-agency.en.png)

Both versions share the same nodes and connectors. Only language-specific
copy and typography differ.

## Hierarchical search control policy

The stage controller may delegate search-budget advice through one
proposal-only seam:

```text
RAG priors + exact DesignState + DesignOptionPortfolio + Evaluator receipts
                              |
                    SearchPolicyRequest@1
                              |
        explicitly registered AsyncSearchPolicy implementation
                              |
                       SearchDirective@1
                              |
       deterministic authority, hard gates, commit/reopen compilation
```

`archflow.control.search_policy` fixes the decision-space kind, exact
project/run/branch/epoch and state digest, candidate/evaluation identities,
objective mean/variance/sample count, finite evaluation/compute/token budget,
allowed actions, and the explicit reopen envelope. A policy can recommend
research, expansion, deepening, resampling, pruning, holding, stopping, or a
request to commit/reopen. It cannot accept a stage, mutate state, persist a
record, write canonical `HEAD`, waive a hard failure, or reopen a lock.

`SearchPolicyRegistry` dispatches by exact `policy_id` and exact immutable
descriptor only. It has no default, family lookup, retry, or fallback. The
string family `ocba` is reserved by `RESERVED_OCBA_POLICY_FAMILY`, but this
repository intentionally contains no OCBA algorithm or registered OCBA
implementation. A future algorithms team can implement `AsyncSearchPolicy`
without changing state, evaluator, commit, or P036 ownership boundaries.

`runtime/hierarchical_search.py` is the first exact-state adapter for that
seam. It derives branch identity from `OperationalMarkovState`, replays the
retained portfolio, hard-check denominator, stage closure, convergence, and
adopted-applicable evidence, then ends at an authority-free proposal. It is
deliberately detached from `DesignController`: commit and reopen remain typed
requests for the existing authority path, not side effects or transitions.

Before later stages can omit a building system, Stage 0 may compile an exact
semantic denominator through `control/genesis_completeness.py` and
`runtime/genesis_completeness.py`. The denominator originates only from the
initial branch epoch in the existing `research_brief` phase. Its members come
only from explicitly supplied brief, typology, human-authority, or adopted and
applicable RAG bases. A RAG basis binds the retained snapshot bytes, exact
query/scope/revision, adopted quote, and applicability record; a URL alone is
not evidence identity.

Each denominator member is assessed as `PRESENT`, `NOT_APPLICABLE`, or
`UNKNOWN`. Not-applicable claims need independent evidence and authority;
unknown never closes the check. Successor stages inherit the exact original
denominator and receipt lineage and cannot re-originate a smaller one. The
result can be bridged to the existing `CheckReceiptEnvelope`, but the bridge
does not infer a complete typology, write project data, attach itself to every
runner, accept a stage, or replace `CompositeStageClosureReceipt`.

## Semantic-to-geometry bridge

The inset between the Primary Architect and the typed operator makes one
design-decision boundary explicit:

```text
one Design Decision
  -> Semantic Component
  -> Realization Specification
     -> Parametric Family | Authored Asset Family | Hybrid Family
     -> GeometryProgram
     -> RealizationReceipt:
        component <-> geometry object <-> platform object
```

The structured scene state is the authoritative input to validation and later
adapters. Screenshots are readonly visual and aesthetic evidence only. A
platform execution receipt therefore cannot accept a candidate or advance
canonical state.

P051 proves the existing identity and compiler invariants at this bridge: one
stable `DesignComponent` tree owns coarse volumes, detailed geometry objects
bind to those component ids, and exact-predecessor refinement preserves
unaffected siblings. P054 now adds a bounded provider-neutral authoring request
whose accepted output is the existing `SpatialOptionProposal@2`; the component
tree and coarse massing volumes are produced together and deterministic
validation rejects stale, cyclic, multiply-owned, unowned, and unknown-source
results. P054 alone does **not** prove that component and detailed geometry
changes persist through an official runtime. P055 compiles those two
successors as one exact-base value transaction: semantic invalidation must be
answered by changed geometry or explicit revalidation, retirements must remove
their exact geometry subtree, and preserved siblings keep direct object
digests. Any semantic or geometry failure exposes no successor. P056 now owns
the P053/P036 CLI, root binding, recovery, and scripted sandbox proof. M050
isolates bypass routes. P057 derives a disposable `ComponentIndex` and bounded
component task context from the exact control tree, `DesignComponent` records,
development dependencies, obligations, semantic bindings, compiled objects,
and lifecycle receipt. The index embeds each authoritative `DesignComponent`
instead of inventing parentage, rejects stale source digests, and can be
deleted and rebuilt without losing design state. P058 closes the fresh
longitudinal proof boundary with a new P036 project: a P053-authorized
deterministic provider produces a coarse radial hall and dome through the
formal runtime, then P055 deepens the same component identities through an
explicit oculus and a bounded coffer field. Every stage has exact lifecycle,
geometry, sandbox realization, artifact-presence validation, and reloadable
P036 evidence. The proof loads no prior Pantheon or sandbox Gold answer.

P060 replaces the remaining artifact-presence-only inference with a separate
project-derived architectural-usability contract. Compilation binds one exact
project/run/base, current developed-design digest, sole selected component
tree, compiled neutral geometry, sandbox scene, realization receipt, and
stable artifact reference. The framework supplies only generic measurable
comparison operators. Criterion vocabulary, values, units, component/object
locality, obligations, and authority remain project records. Retrieved text is
never authority by itself: a distinct authorized project adoption record must
bind it before it can supply a mandatory criterion. Architectural acceptance
requires artifact presence plus every mandatory criterion to be evaluable and
pass; missing, stale, unsupported, unit-incompatible, locality-mismatched, or
contradictory evidence becomes a typed failed or unknown local finding. The
receipt is read-only and cannot edit geometry, certify a model assertion,
review a candidate, promote state, or write canonical state. P058 remains an
honest artifact-presence-only proof rather than being retroactively upgraded.

P061 makes “family” a typed annotation of that same component–geometry value,
not another ownership tree. A parametric family references parameters and
hosted assemblies already present in generic geometry operations. An external
mesh family references immutable content-addressed assets with explicit native
unit, socket, frame, and scale while exposing no internal parametric edit
surface. A hybrid can carry both under one existing `DesignComponent` binding.
All modes publish the same project/base, family revision, anchors, interfaces,
dependencies, semantic bindings, objects, and provenance receipt.

The family compiler only validates an already compiled program. A separate
read-only realization receipt then binds that compilation to the exact
sandbox receipt, scene, workspace, and unchanged family object digests.
Revision, replacement, and retirement are checked against the exact P055
predecessor/current lifecycle; changed content cannot hide behind one revision,
and preserved dependents cannot ignore a changed declared dependency. P036
remains the only writer. P061 provides neither an architectural family
catalogue nor a downstream platform exporter.

P062 adds a detached experiment protocol, not another runtime controller.
Preregistration freezes separate P036 project cases, a complete condition
matrix, one shared provider profile, attempt and wall-clock bounds, metric
specifications, and code/contract identity before any result. Attempt intents
make planned, running/interrupted, terminal, provider-failed, and timed-out
states distinguishable. Full runs retain P053 receipts; generation-context
ablations declare only named withheld context; validation ablations reuse an
exact source attempt without provider replay. Outcomes preserve P060 and other
source statuses, report required missing measurements as unknown, and have no
provider, validation, persistence, review, promotion, or canonical-write
authority. Concrete building cases and all derivation traces remain in their
project probes.

The P062 result index is rebuildable from P036 records rather than authored as
a report. It distinguishes planned, running, terminal-unmeasured, failed, and
completed assignments, binds every indexed payload digest to its study record,
and refuses successful post-completion retries. Its table-ready state stays
false until every preregistered assignment has an exact outcome, so an unrun
cell cannot enter P063 as empirical evidence.

Condition completion is schema-bound as well as role-bound. Preregistration
fixes each terminal role's source receipt schema and accepted status. Full and
generation-context conditions retain the same authority chain; validation
ablations remove only named evaluator roles and reuse every other binding
exactly. An experiment-local placeholder therefore cannot impersonate P056,
P060, or P061 evidence.

Before a completed attempt is persisted, the P062 adapter also reconciles all
known terminal receipts as one exact case chain. Geometry, design-state,
component-tree, scene, sandbox-receipt, family-set, and family-compilation
digests must agree across P056, sandbox, P060, and P061. Empty family
compilation is a valid P061 no-op in isolation but not completed family evidence
for this experiment.

Until that chain is complete, three routes must not be conflated:

- the intended active route is typed semantic-spatial authoring, joint
  semantic-geometry lifecycle compilation, P053 provider resolution, and P036
  persistence;
- Fake Architect/FakeVoxel and arbitrary MCP-plan paths are compatibility or
  test routes only and have no production authority;
- `python -m archflow.runtime` delegates to the formal P056 CLI; active Primary
  Architect and geometry authoring accept only P053-authorized providers, while
  downstream Minecraft work begins at a typed neutral-package export request;
- P026/P038 and later probes are evidence packages, not frozen design answers
  that production may reload as generated output.

## One protocol, two state scopes, one evidence trace

V4 separates three things that the earlier diagram conflated:

| Object | Scope | Authority | Purpose |
| --- | --- | --- | --- |
| `C_v` | Project version | Canonical, single-writer | The accepted baseline from which a new design branch starts |
| `D_v,k` | One candidate branch | Working, exact-base, no canonical writer | The operationally Markovian state sufficient for the next design move |
| `H<=t` | Whole run history | Append-only evidence | Audit, attribution, reconstruction, training, and recovery |

`C_v` and `D_v,k` are not competing sources of truth. `C_v` owns formal project
authority. `D_v,k` owns only the current branch's design reasoning. A candidate
may pass through many `D_v,k` states before the Architect chooses to submit it;
only an accepted submission can produce `C_v+1`.

The trace is deliberately separate. State is sufficient for action; trace is
necessary for justification. Loading a trace or deterministically rebuilding a
state does not prove that external MCP or LLM execution can be replayed.

Architectural design is not naturally Markovian. V4 constructs a bounded,
operationally Markovian approximation by compiling every accepted design move:

```text
natural-language move
  -> DecisionOperator@2 bound to exact D_v,k
  -> StateDelta@2
  -> precondition and lock checks
  -> apply direct effects
  -> typed dependency / invalidation closure
  -> conditional and blocker readiness compilation
  -> OperationalMarkovState@3 D_v,k+1
```

The decision is the action, not the state. The next state contains only its
future-relevant consequences: factorized brief, semantic, geometry, parameter,
decision, evaluation, unknown, deliverable, budget, and capability facts;
bindings and locks; commitments and obligations; dependency edges;
invalidations; evidence references; phase; and exact branch/canonical base.
Closure follows only explicit `invalidates` and `requires_revalidation` edges
to a bounded fixed point. `blocks` controls obligation readiness and
`supports_only` remains queryable; neither can falsely invalidate a fact,
commitment, or obligation. The compiler never guesses an unnamed architectural
dependency or prescribes the repair answer.

`state_digest` includes branch epoch and is used for exact-base rejection.
`sufficient_digest` excludes branch history and permits a stronger test: two
history-distinct states with the same future-relevant content must admit an
equivalent rebased operator result. Neither digest gives `D_v,k` canonical write
authority.

The trusted provider adapter receives only the request and a bounded authority
token. Lifecycle qualification and activation require a separate reconciler
object capability; the public router facade carries no mutation or signing API.
Control-plane-issued authority tokens and frozen invocation envelopes are
HMAC-authenticated for the life of that in-memory control plane; portable
provider receipts reject machine-local paths and non-JSON object keys before
downstream reconciliation. An adversarial provider must remain out of process
behind the trusted adapter because Python naming is not a security sandbox.

### P042 compiled-building probe

P042 tested the compiler against one research-capable Agent's project-scoped
Pantheon derivation. The raw request remained the only authored input. Four
exact-base operators compiled:

```text
sourced evidence
  -> mutually exclusive target branches
  -> low-resolution current-precedent packet
  -> measurement conflict and local invalidation
```

The result is a reloadable `research-brief` dossier, not a generated building.
It demonstrates that the compiled state can retain provenance, distinguish
secure facts from unresolved questions, prevent an Agent from silently choosing
human design authority, and invalidate only named downstream consumers while
preserving unrelated work. It also exposes present schema limits: epistemic
fact status and conditional/blocked obligation status were retained in mapping
receipts rather than first-class P041 fields, and no maturity transition could
be authorized before P039.

M009 closes that state-schema defect with `OperationalMarkovState@3`,
`DecisionOperator@2`, and `StateDelta@2`. Fact value and epistemic status are
separate, conditions and blockers compile obligation readiness, and every
dependency carries an explicit propagation effect. The immutable P042 run
remains a readable V2 compatibility record; automatic migration fails closed.
A separate `compiled-state-002` run explicitly recompiles the same evidence:
exactly the two measurement-dependent deliverables become stale, the resolution
obligation remains open, dependent repair obligations remain blocked, and the
unrelated plan-relation output remains current.

M012 repairs the existing-commitment application seam without changing the
operator schema. A same-ID commitment value is accepted only when replaying the
P015 lifecycle transition function with the operator's named authority produces
that exact successor. This permits authorization, release, revision, and
supersession while arbitrary replacement, immutable-policy revision, and
unauthorized lifecycle changes remain identity collisions.

The probe deliberately leaves canonical `HEAD` unchanged, materializes no
geometry, omits the V3 visual package until geometry exists, and denies
generation, usability, and external execution-replay authority. All building
facts and traces remain below `probes/test_pantheon/`; this experiment adds no
building answer to the framework.

P042 also stopped on a real authority question: whether the request means a
historical reconstruction, a precedent-inspired new building, or a voxel
translation. P043 now implements the phase-independent clarification boundary:
an open obligation becomes a project/run/branch/state-bound
`ClarificationRequest@1`; a named human response becomes a single-use,
time-bounded `AuthorityDecisionReceipt@1`; and only a validated receipt can
compile an exact-base `DecisionOperator@2`. Selection, open revision, and
commitment lifecycle actions pass through normal state closure. Self-answer,
stale, duplicate, expired, cross-project, cross-branch, and unauthorized
responses fail closed. Decline, timeout, and no response retain the same
reloadable blocked state.

The clarification schemas explicitly deny hard-gate waiver, candidate
approval, promotion, world mutation, geometry, evaluation, and deliverable
authority. This boundary therefore remains upstream of P025 candidate
preview/undo and M004 final approval instead of overloading those late-stage
controls.

The older `archflow.runtime.operational_transition` path remains readable only
as an explicit `OperationalMarkovState@1` compatibility trace for P008/P009.
It lacks bindings, locks, commitments, dependencies, phase deliverables,
evaluations, and uncertainty, so new controllers must reject it and recompile
authoritative project records into V3. Automatic migration is forbidden because
it would have to invent precisely the future-relevant state the compiler is
meant to preserve.

## Upstream brief and program compilation

The raw user request is not required to contain a width, depth, room list, or
finished architectural program. The upstream compiler may use attached material,
CLI retrieval, current canonical commitments, and bounded expert advice to
derive building-scoped hypotheses:

```text
raw request and evidence
  -> users and activities
  -> candidate functions and capacities
  -> area ranges and relationship hypotheses
  -> proposed commitments and open obligations
  -> initial branch-local design state D_v,0
```

A user-supplied size is preserved as an authoritative constraint only when its
source and revision policy justify that authority. Otherwise size is a later
design result. Concrete dimensions may exist in a building case as explicit
input or as a derived, receipt-bound output; they must never become a framework
default.

### Mid-design requirement changes

A new user requirement is an input event and potential transition, not an
in-place edit to `D_v,k`:

```text
new requirement event
  -> source and authority classification
  -> proposed fact / commitment / authorized revision
  -> exact-base conflict and lock checks
  -> typed dependency impact
  -> local invalidation and repair obligations
  -> optional P039 backward phase transition
  -> D_v,k+1
```

A compatible addition may keep the current phase. A tighter constraint may
invalidate only its named consumers. A contradiction with an active or locked
commitment pauses for P043 authority rather than overwriting it. The current
canonical `C_v` does not change until a later candidate passes its independent
review and single-writer promotion; P018 retains both the prior requirement and
the addition/revision event for reconstruction.

Reusable protocol, schemas, relation terms, and deterministic compilation rules
belong in V4. Retrieved facts, capacities, area choices, topology, dimensions,
and their derivation trace belong to the individual building run.

## Framework and probe boundary

`archflow/` owns all reusable mechanisms: schemas, compilers, reducers,
capability discovery, validation, archive formats, execution boundaries, and
the generic runner.

A concrete building uses one project envelope at either an explicit active
runtime root or an explicitly promoted probe root:

```text
active:   <workspace_root>/projects/<project_id>/
promoted: probes/<project_id>/
```

Both locations have the exact same P036 project envelope:

```text
<project_root>/
├─ project.json          immutable project identity and format schema
├─ HEAD                  mutable canonical pointer; single-writer CAS only
├─ input/                authorized external inputs; never generated answers
├─ objects/sha256/       immutable content-addressed records and artifacts
├─ events/               append-only project history H<=t
├─ canonical/            verified accepted C_v snapshots
├─ runs/<run_id>/        D_v,k branches, records, candidates, and workspaces
└─ exports/              non-authoritative share packages
```

`input/` may contain only the raw request, authorized evidence, explicit
external constraints, site observations, and run policy. Everything else is a
versioned framework output. Candidate work remains under its run; only the
single-writer commit boundary may append a canonical snapshot and event, then
compare-and-swap `HEAD`. No persistent building output belongs in a
repository-level `.runs/` directory.

P052 selects the physical root without changing logical identity or authority.
`RuntimeConfig@1` supplies absolute, distinct workspace, cache, and temp roots;
it rejects any overlap with the source checkout. Only
`workspace/projects/<project_id>/` may become a project envelope, and P036
remains its sole writer. Cache and temp are explicitly non-canonical. A Codex
Cloud `/tmp` runtime is disposable setup state, not durable project evidence.

Active runs are not automatically copied into Git. Promotion into
`probes/<project_id>/` is a separate reviewed operation that preserves content
identity and selects compact regression or publication evidence. Existing
probes remain frozen baselines. The zoning-city comparison that motivated this
boundary is recorded in
[`mapping/P052-zoning-city-to-building-runtime.md`](mapping/P052-zoning-city-to-building-runtime.md).

The side-effect-free contracts in [`archflow/project/`](../archflow/project/)
define this identity, layout, logical-reference, and persistence-port boundary.
P035's manifest, reference, layout, and port definitions remain
side-effect-free. P036 supplies the only generic filesystem implementation:
immutable digest-bound record/blob ingestion, exact-base run manifests,
accepted-event preparation, atomic `HEAD` compare-and-swap, verified reopen,
and orphan reporting. A producer with no assigned destination must stop and
ask the project owner rather than invent a path.

M007 makes canonical identity `ProjectVersionRef(project_id, version, digest?)`.
For current format-version-2 project documents, that digest has one meaning:
the canonical state-content digest shared with the P018 reducer. The immutable
snapshot file has an independent `ProjectRecordRef.sha256`; `HEAD` and
`ProjectEvent@2` bind both identities explicitly. Format-version-1 documents
remain readable only through a named legacy branch where the historical
snapshot-digest meaning is preserved and never silently reinterpreted.
`RunRef` and `BranchRef` remain separate exact-base identities. Production
initialization accepts typed commitments and a project-owned design-program
reference, but no `GoalContract`, fixture defaults, or `BuildingProgram@1`
answer. The old program schema remains only as `legacy_program_view` for
validator compatibility and is not automatically exposed to experts.
Production claim gates compile only authorized hard commitments.

Project promotion writes immutable decision/candidate evidence first, then the
replacement canonical snapshot, then its accepted event, and replaces `HEAD`
last. A pre-`HEAD` crash leaves non-canonical orphans that are reported on
reload; a stale or rejected writer fails closed. This boundary does not claim
atomicity with a Minecraft world.

A probe is therefore data, not a second codebase. It cannot provide a custom
Architect, expert schedule, room/dimension/material/topology defaults,
geometry script, validator, reducer, or archive loader. The dependency
direction is:

```text
generic ArchFlow runner -> probe input and output storage
archflow -X-> case-specific code
probe data -X-> framework code
```

`bootstrap_raw_request_project` proves this direction with
`probes/test_pantheon/`: the instance supplies one raw request, while the
generic project module creates the manifest, initial canonical state, event,
`HEAD`, run, input record, and non-authoritative bootstrap receipt. This is
project initiation and task-brief retention, not program derivation, spatial
design, candidate generation, or Minecraft validation.

Framework tests may use neutral mechanical fixtures. Cross-case behavior is
proved with at least two non-isomorphic data packages, but neither package can
become a fallback answer.

### Production responsibility control plane

Provider registration and production authority are separate. P053 defines one
provider-neutral responsibility control plane with one contract owner, zero or
one active provider, explicit `registered -> shadow -> verified -> active ->
retired` lifecycle, and a monotonic authority epoch. Shadow providers may see
the same bounded request but receive neither production nor canonical-write
authority.

An exact-base handover prepares its complete receipt before replacing the
binding, retires the previous provider and activates the verified target in one
critical section, and increments the epoch. Provider results must be checked
against the current provider identity, epoch, and binding digest after execution
so an in-flight result cannot survive a cutover. Active-provider failure is a
named unavailable outcome; it never calls a shadow or retired fallback.

This is a control-plane contract, not another persistence path. Its states,
tokens, and receipts are typed return values; P036 remains the only project
writer. P056 adds async active and shadow invocation with the same post-call
epoch and binding validation, plus an `AuthorizedAsyncModelProvider` that
checks the P053 envelope, exact model request, and provider identity before a
model receipt re-enters P029/P054 code. The authority token remains inside the
trusted adapter closure and never reaches Agent CLI or a future API adapter.

The P056 persistence loop retains an initial root binding or successful P055
transaction as immutable
P036 run records and then writes one reference-only checkpoint. Its intent
digest is computed from the exact run base, step id, immutable raw-request
reference, and current-context record references, so resume can short-circuit
before provider invocation. A validated P053 envelope is evidence in that
record set; Agent CLI and later API adapters share the same activation function
and `AsyncModelProvider` port. Orphan run records from a pre-checkpoint
interruption never become completion evidence.

P059 makes the unsuccessful side equally durable without turning it into a
success path. `ModelInvocationReceipt@2` binds bounded wall-clock duration to
the exact request, provider identity, status, byte/token counts, and
output-or-error evidence; historical `@1` receipts remain read-only inputs
with an explicit unknown duration represented as zero. After a compiler stops
on one or more P053-validated model receipts, P036 stores a
`ProductionFailedAttemptReceipt@1` under that run. The receipt has no lifecycle
successor, transition checkpoint, alternate provider, persistence authority,
or canonical-write authority. Reload verifies the embedded receipt digest and
orders later attempts through project-local retry references. A completed
intent cannot subsequently acquire a failed attempt, while a failed intent may
still be retried and reach the ordinary successful checkpoint path.

`archflow-runtime run-project` is the formal P052/P053/P036 entry. It ingests
an explicitly supplied typed current-project context into P036, generates two
P054 semantic/coarse-geometry alternatives, compiles the existing option set,
records one model Architect selection, initializes the existing developed
design branch with unresolved discipline obligations, authors P050 neutral
geometry, and realizes it in the deterministic sandbox before the root
checkpoint is published. Resume reuses the durable context and checkpoint and
does not call the configured provider. This is not permission to infer missing
site, program, or policy facts from an empty prompt; those remain current
project state. M050 makes direct Primary Architect injection fail closed,
prevents marked test providers from activation, and keeps raw Minecraft plans
behind the compatibility method while `preview_export` and `build_export`
require `MinecraftExportRequest@1`. P058 supplies the fresh Pantheon-scale
scripted-provider proof and records live Agent CLI as not run; its accepted
sandbox disposition proves artifact presence only, not architectural
usability, structural performance, fabrication, or external-platform
equivalence.

### Compaction recovery boundary

P046 remains the source of bounded Agent work context. M033 connects that
read-only capsule to Codex lifecycle hooks without making a transcript or hook
output authoritative. `PreCompact` regenerates every active-card capsule and
fails closed if recovery cannot be built. After compaction,
`SessionStart(source=compact)` injects only the current commit, dirty path names,
active-card planning contract, verification label, and capsule digest before
the immediate continuation.

The recovery hook reads no transcript and writes no checkpoint, project record,
cache, or temporary artifact. It does not select work, claim completion, or set
a model context window. The compacted summary and injected context are
orientation only; current repository state and machine verification remain the
authorities. Project-local hooks require explicit Codex trust review.

### Test hierarchy

Framework unit tests under `tests/` verify contracts, reducers, authority
guards, and failure behavior. Their temporary files are disposable test
mechanics, not building artifacts.

A committed building integration test is different: it is opened as
`probes/<project_id>/`. The generic runner reads only `input/`, invokes public
`archflow` modules, and writes candidate state, receipts, workspaces, artifacts,
and the final run manifest only under that project. The case contains no Python
package marker or alternative implementation. The retained synthetic
`test_library` smoke intentionally exercises only `input/` and
`runs/<run_id>/`; it demonstrates storage direction without claiming
architectural design intelligence. Its compatibility adapter may emit a local
file URI only inside the supplied speculative workspace. Before retained
records are written, containment is checked and that transient location
becomes a project-relative logical URI, so committed evidence remains portable
across repository roots and machines.

## Design maturity graph and inner operational loop

The workflow is not a flat agent swarm. `D_v,k` carries one explicit design
maturity phase:

```text
research / brief
  -> programming
  -> site and resource coordination
  -> schematic design (方案)
  -> design development (扩初)
  -> candidate coordination
  -> execution ready
```

This is a dependency graph for deliverables, not a fixed list of experts.
Forward movement cannot skip a phase. Backward revision is allowed, but it
invalidates affected downstream deliverables and creates new obligations
before work resumes.

P039 implements this boundary as a companion
`DesignMaturityState@1` projection for each operational `D_v,k`. The projection
names the exact branch, operational-state digest, current phase, and typed
project deliverable references. Each deliverable retains the branch-local
state digest that produced it; later states may continue to reference it until
a typed dependency marks it invalid or in need of revalidation. Project
content stays in the project repository or probe, while the framework stores
only generic roles and transition rules.

The deterministic exit requirements are:

| Current phase | Required role before advancing |
| --- | --- |
| research / brief | research brief |
| programming | design program |
| site and resource coordination | site context and build policy |
| schematic design | schematic options and an explicit selection |
| design development | coordinated design-development package |
| candidate coordination | coordinated candidate, hard-usability receipt, and commit authorization |
| execution ready | terminal; no implicit further phase |

`PhaseGateRequest` is exact-base and may target only the immediately following
phase. `PhaseGateReceipt@1` is reproducible from the typed roles and refuses
expert self-certification. A receipt becomes stale as soon as its branch epoch
or operational state digest changes. P022 will consume the receipt when it
integrates the resumable controller; P039 does not create a second canonical
store or bypass the decision compiler.

When a requirement changes mid-design, `compile_backward_revision()` starts
from the named changed references and walks only dependency edges typed
`INVALIDATES` or `REQUIRES_REVALIDATION`. It returns the affected downstream
deliverables and one open repair obligation per affected result. `SUPPORTS_ONLY`
context and obligation-to-obligation `BLOCKS` edges are deliberately excluded
from staleness propagation. This is the concrete locality benefit of the
compiled Markov state: unchanged, dependency-independent work remains current,
while justification and full history stay in the event trace.

The primary Architect reads the current `D_v,k` and selects the next move.
Within the current phase, open obligations and available evidence drive
capability discovery:

1. discover relevant design experts from `D_v,k`;
2. obtain detached, exact-base advice or bounded failure receipts;
3. let the primary Architect propose a local delta or replacement;
4. treat the proposal as pending, not as a fact;
5. compile it into a typed exact-base operator and propagate named dependency
   closure, or accept a separately verified observation;
6. admit only the resulting compiled consequences as `D_v,k+1`;
7. rediscover capabilities from the resulting state.

Program, capacity, site, structure, circulation, envelope, material, and other
experts therefore live inside the design loop, but each capability declares
the phases in which its output is admissible. They advise the Architect; they
do not form a fixed within-phase scheduler and cannot write canonical state.
Phase-aware discovery intersects the current phase with current obligations,
available evidence, and capability metadata. Its stable id sorting is only a
reproducible presentation; the Architect may choose different valid
within-phase orders and must cite the advice it actually adopts.

The distinction is strict:

- P020/P021 produce brief, function, capacity, area, and relationship records;
- P032/P034 coordinate site and resource constraints before spatial proposals;
- P023 produces schematic topology, levels, massing, and footprint options;
- P040 develops the selected schematic across structure, circulation,
  envelope, material, use, and constructability;
- P024 alone assembles a coordinated developed design into a candidate and MCP
  handoff.

An early expert may flag downstream risk, but cannot certify or produce a
later-phase deliverable. A later-phase failure may send the Architect backward;
it cannot be papered over by calling the phases in another order.

MCP, CLI, model, and storage adapters are capabilities, not architectural
authorities. Tool success proves only that an operation or candidate artifact
was produced. It cannot prove usability, waive a commitment, select an
aesthetic winner, or advance `C_v`.

## Outer candidate review and promotion

The boundary becomes strict when the Architect creates a
`CandidateSubmission`. Three read-only questions remain separate:

- **Hard usability:** Is formal promotion permitted at all?
- **Commitment completion:** Does the candidate satisfy active authoritative
  commitments and discharge the relevant obligations?
- **Soft multi-objective evaluation:** What qualities and trade-offs does the
  candidate exhibit?

Hard failures and commitment violations return evidence-bound obligations to
the design loop. Soft observations may inform a revision or human preference,
but cannot waive a hard failure or automatically declare a unique winner.

Only the committer may compare the exact base version, accept the read-only
decision package, append a commit event, and advance `C_v` to `C_v+1`.

## Human and player authority

The player-facing layer is not a cosmetic front end. It owns explicit
confirmation and reversible control where authority is genuinely human:

- resolve ambiguous requirements and approve changes to locked commitments;
- inspect program, area, and topology rationale;
- preview, reposition, revise, pause, cancel, and undo a candidate;
- view material requirements and unresolved risks;
- make aesthetic or final-selection choices without bypassing hard usability.

## Current executable boundary

The repository already proves portions of the outer control boundary:

- immutable canonical state and exact-base candidate submissions;
- isolated workspaces and adapter failure containment;
- read-only voxel observation;
- deterministic minimal usability gates;
- state-responsive expert registry mechanics;
- read-only multi-objective aesthetic observations;
- bounded obligation-driven repair and single-writer in-memory promotion;
- a provisional branch-local operational transition trace;
- side-effect-free `OperationalMarkovState@3`, `DecisionOperator@2`,
  `StateDelta@2`, structured epistemic facts, conditional/blocked obligation
  readiness, exact-base/lock/precondition checks, and typed local dependency
  closure;
- explicit fail-closed V1/V2 compatibility boundaries that prevent old traces
  from masquerading as complete V3 design state;
- a project-scoped Agent compilation probe that reloads a sourced conditional
  research dossier and proves local downstream invalidation without geometry,
  canonical promotion, or usability claims.

P042 falsified the stronger claim that V2 retained every future-relevant
research and obligation distinction. M009 repairs those known losses; it does
not claim that every future architectural distinction has already been found.

M062 places failed P060 evidence back into the same semantic–geometry
production lifecycle without adding another ownership tree. The model authors
the complete semantic successor and its geometry delta. Deterministic code
requires geometry for every directly changed component, publishes exact
predecessor object and binding revision tokens, and may revalidate only
descendants whose predecessor and successor geometry subtrees are identical.
When deterministic geometry compilation rejects a complete typed model edit,
the next bounded model request includes the exact rejected output, its digest,
and the exact issues. The next output is still a complete replacement over the
original predecessor: runtime code neither merges model rounds nor authors the
repair. This preserves the provider's intended assembly while keeping compiler
and validation authority deterministic.
Study 025 exercises that contract across a process boundary: one P036-loaded
rejected round led to one real provider replacement that retained its door
assembly, compiled, and realized one sandbox opening. That recovery is not a
committed lifecycle transition and has not yet passed the subsequent P060 gate.
Study 026 then closes the deterministic continuation without another model
call: P055 compiles and checkpoints the exact semantic-geometry successor, and
P060 recomputes four project-derived measurements on a fresh realization. All
mandatory clinic criteria pass, including one usable opening; no family or
multi-building result is inferred from that single revision chain.
`SpatialOptionProposal.components` remains the building ownership tree;
`DesignStateTree` remains control and context. Failed proposals and provider
receipts are retained through P036 and never promoted as committed state.

It does **not** yet prove raw-prompt program derivation, capacity/area
compilation, relationship/topology generation, a complete Architect controller,
commitment-criterion monitoring, a phase gate, durable event-sourced canonical
storage, candidate-stage player controls, or an accepted prompt-to-usable
Minecraft slice.
Those gaps are tracked explicitly in the dynamic map rather than implied by the
diagram.

## The decision spine

The additions of 2026-08-28 (live providers, precedent adoption, staged
declarations, typed repetition) are one structure seen from different
sides. Straightened, the architecture has one vertical spine crossed by
one horizontal axis.

### Vertical: knowledge → requirement → decision → geometry → validation → lock

Retrieved knowledge has exactly one door to authority — a typed adoption
record (P060 for criteria, P067 for precedent facts). Every inlet (P028
CLI retrieval, P020 brief evidence, P042 research dossiers, P067 web
snapshots) is provenance-bound evidence with no authority of its own, and
raw retrieved text never enters a provider prompt.

One adopted fact then projects into up to four requirement forms, each
firing at its own checkpoint, all chaining to the same provenance:

| Form | Checkpoint | Mechanism |
| --- | --- | --- |
| Build-policy constraint | before generation | provider must respond (M057 required responses) |
| Stage declaration | at the phase gate | provider must declare values; the gate cross-checks the authored geometry (P068) |
| Architectural criterion | after realization | deterministic P060 measurement of the artifact |
| Commitment | after the gate passes | P015/P016 lock; reopening needs authority plus the P063-proven dependency-scoped repair |

These four are not redundant channels but the same requirement at four
moments of its life: advise the generator, gate the stage, measure the
result, lock what must not silently move.

### Horizontal: stages ordered by irreversibility

Three stage notions align into one axis: project-level `DesignPhase`
gates (P039) are the gates; per-component `ComponentMaturity` marks
subtree progress; P055 exact-predecessor transactions are the only way
maturity advances. Declaration contracts attach to the P039 gates as
typed, quadrant-grouped deliverables — the gate's deliverable slot was
always the mandatory-declaration slot; P068 makes it typed.

Quadrants follow the shearing-layer irreversibility ordering (site >
structure and massing > skin and openings > space plan > detail), and the
practice codes agree on where each quadrant must close: concept sign-off
fixes massing and the structural grid (RIBA stage 2, AIA schematic, the
Chinese scheme-design depth code); spatial coordination closes skin and
services routing before technical design; detail stays free the longest.
The rule the spine adds: when a phase gate passes, the declarations of
its irreversible quadrants compile into hard commitments; reversible
quadrants remain stage-local declarations. Irreversibility is therefore
runtime behaviour — what it costs to reopen — not documentation.

### Two lanes, one protocol

The scripted lane (P058, P065) proves protocol capacity; the live lane
(P062, P066) measures model capability. They share every contract and
differ only in the provider. Context-channel ablations (P062
relationships, P067 precedent) measure what each knowledge channel
causally contributes; the P068 gates measure what the model commits to
before its work may pass.

### Dependent decisions are derived, not restated

A decision that depends on another must be computed from it, and must
bind the commitment that carries it. The monument's asymmetric front
elevation (P074's origin defect) showed both halves failing at once: the
column-row origins restated the axis as free literals — off by 0.25 m,
the beam row by 0.90 m, the plinth by 0.50 m — and nothing responded to
the axis, so no revision would ever have reopened them, and no criterion
measured the symmetry the axis implies.

The rule the spine adds: a dependency edge between decisions is
executable in both directions. Forward, the dependent value is derived
from the basis (`row_origin = axis − ((count−1)·step + width)/2` — the
number cannot drift because it is not stored). Backward, the dependent
geometry's semantic binding names the basis commitment, so revising the
basis reopens exactly its dependents through the existing binding
closure. And the self-check is compiled from the same basis: the axis
commitment yields an axial-symmetry criterion over the named front
groups, measured on realized bounds, never on intent. One basis, three
projections — derivation, reopening, criterion — mirroring how one
adopted fact projects into constraint, declaration, and criterion.

### Build-to-measure is not pass-the-gate

A criterion that lives outside the acceptance path is commentary. The
P074 symmetry criterion first existed as a standalone measurement — it
produced honest FAIL findings over retained scenes, but nothing stopped
a failing realization from being archived ACCEPTED. M075 draws the
line: realization in the workspace is measurement substrate (the model
must be built to be measured), and acceptance is a separate act the
criterion guards. The gate record persists on both outcomes; a failing
stage writes REJECTED and raises a typed stage-gate error, so an
ACCEPTED record for a failing realization cannot exist; a passing
stage's ACCEPTED archive cites the gate record that guarded it.
