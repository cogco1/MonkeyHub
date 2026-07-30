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

A concrete building investigation lives under `probes/<project_id>/`. Its
production project envelope is:

```text
probes/<project_id>/
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

### Test hierarchy

Framework unit tests under `tests/` verify contracts, reducers, authority
guards, and failure behavior. Their temporary files are disposable test
mechanics, not building artifacts.

A building integration test is different: it is opened as
`probes/<project_id>/`. The generic runner reads only `input/`, invokes public
`archflow` modules, and writes candidate state, receipts, workspaces, artifacts,
and the final run manifest only under that project. The case contains no Python
package marker or alternative implementation. Until P020 and P018 implement
the production manifest and persistent canonical/event stores, the synthetic
`test_library` smoke intentionally exercises only `input/` and
`runs/<run_id>/`; it demonstrates storage direction without claiming
architectural design intelligence.

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

It does **not** yet prove raw-prompt program derivation, capacity/area
compilation, relationship/topology generation, a complete Architect controller,
commitment-criterion monitoring, a phase gate, durable event-sourced canonical
storage, candidate-stage player controls, or an accepted prompt-to-usable
Minecraft slice.
Those gaps are tracked explicitly in the dynamic map rather than implied by the
diagram.
