# Runtime

Coordinates a bounded session for the primary Architect Agent: establish the
canonical baseline, open an isolated workspace, expose discoverable
capabilities, and hand a Candidate Submission to the formal boundary.

It must not prescribe a fixed design sequence or hard-code an expert pipeline.
See the current flow in
[`docs/DYNAMIC_MAP.md`](../../docs/DYNAMIC_MAP.md).

Current status: P1 `run_once` proves one bounded attempt: fork workspace, let a
Fake Architect build, validate, evaluate, and either reject without drift or
commit exactly one version. This is a walking skeleton, not a production
orchestrator.

## Obligation-driven repair

`repair_loop.py` keeps one immutable canonical baseline while opening a fresh
speculative workspace for each attempt. Hard and selected-expert findings are
converted into receipt-backed obligations; only the primary Architect chooses
to revise locally, replace the candidate, or declare an unresolved trade-off.
The controller never turns a finding into a geometry edit.

Expert discovery is called from the current detached obligation/evidence
context. Its result is recorded as a set with receipt references, not as a
mandatory invocation sequence. Expert failure is fail-open and cannot mutate
canonical state.

The loop stops on a stale base, wall-time or iteration budget, repeated hard
findings, or two unchanged artifact/obligation transitions. Only an accepted
review bound to the exact candidate and base is passed to the existing
single-writer `Committer`, after which the loop stops immediately.

## Tool and environment feedback

`environment_feedback.py` treats an MCP preview failure as a detached
observation of an attempted transition. An invocation intent freezes the exact
base, workspace, capability, plan, and plan digest before the call. A failure
can become an actionable repair obligation only when that binding is proven
and both canonical and external world mutation are false.

The compiler states the unresolved design relationship without selecting a
foundation, grading operation, site move, or other geometry response. It
builds a detached expert-discovery snapshot from the current obligation; the
registry returns relevant capabilities as a set, while the primary Architect
retains the choice to revise, replace, or declare the issue unresolved.

Environment feedback never produces a P005 hard-usability verdict, never
invokes P007 promotion logic, and has no canonical writer. An identical failed
plan can produce a bounded stop receipt instead of being blindly retried.

`run_repair_loop` exposes an optional fail-closed Architect-error recovery
seam. A handler may return `ArchitectFailureRecovery` only when it can prove
the current base and workspace and provide either receipt-backed obligations
or a retry-stop receipt. The failed iteration records no Architect action and
no candidate; the next iteration consults experts from the new obligations and
returns the choice to the Architect. Missing or invalid binding preserves the
original `architect_error` behavior, while an identical failed plan exits as
`environment_blocked` without review or commit.

Archive and checkpoint schemas are framework mechanisms and must remain
building-agnostic. A probe stores the framework-produced instances but cannot
implement its own loader, reducer, recovery policy, or generation path.
Production modules must not import a probe.

## Intent commitment compilation

`commitment_compiler.py` is the deterministic authority boundary between
language/retrieval interpretation and canonical commitments. It receives
bounded candidate `IntentTerm` values with evidence; it does not parse a
building type or retrieve facts itself.

One explicit interpretation becomes one proposed commitment. Ambiguous
target/minimum/maximum meanings remain separate proposals, and missing
evidence remains unknown. Every proposal still requires named authorization.
An authorized proposal becomes an active `LockedCommitment`; later prompt text
cannot replace it without an explicit, authorized revision event that preserves
predecessor/successor lineage.

The compiler has no project writer. P020's `brief_compiler.py` now consumes
bounded, explicitly typed observations from the raw request, retrieval, or a
model interpreter. It produces `DesignBrief@1`: project/base-bound claims,
proposed constraints and commitments, open obligations, and a status for every
generic upstream slot. It never parses a building type into a room list or
selects area, topology, or geometry.

Every compiled claim and constraint carries its evidence references, compiler
identity, and exact base digest. Cross-project evidence, stale provenance, and
cross-slot references fail closed instead of silently joining two design
states.

## Program hypothesis compilation

P021's `program_compiler.py` consumes typed proposals from a model, retrieval
capability, or expert and compiles `DesignProgram@1`. The framework contains no
building-type-to-room-list table: every user group, activity, function,
capacity, area, and relationship must cite evidence from the current
exact-base brief.

Numeric program outputs are non-point ranges. Their assumptions are first-class
records, so a later change can invalidate the dependent ranges and
relationships without discarding unrelated state. Capacity, net area, gross
allowance, footprint, and total floor area remain different metric kinds.
Adjacency, separation, public/private, noise, and circulation remain
non-geometric hypotheses; none selects coordinates, levels, topology, massing,
materials, or a candidate.

When scale is unknown, the compiler accepts either no number plus an open
obligation, or multiple named bounded scenarios. It rejects a single hidden
default. Brief constraints are retained by reference and are never silently
converted into program facts. The compiler has no project writer, MCP handle,
hard-gate authority, or canonical committer.

## Site context compilation

P032 separates authorized site observation from architectural response.
`site_observation.py` validates a detached tool result against an exact project
base, named world and dimension, read-authorized envelope, and anchor. A stale,
cross-world, cross-dimension, out-of-envelope, or write-authorizing payload
fails before it becomes design state.

`site_compiler.py` produces `SiteContext@1` with the authorized and observed
envelopes, approaches, sampled ground model, protected cells, observation
digest, unknowns, and open obligations. Superflat is an explicitly observed
ground kind supported by samples; missing evidence remains unknown. Uneven
ground and incomplete access create obligations for the Architect rather than
selecting grading, relocation, foundations, orientation, or geometry.

The site adapter is read-only and has no MCP client. The compiler has no world
handle, mutation authority, project writer, or candidate selector. Later
spatial proposals must consume this context and remain within its authorization
and protection boundaries.

## Resource and constructability policy

P034's `resource_compiler.py` combines the exact-base brief, `DesignProgram@1`,
and `SiteContext@1` with one explicitly authorized build-policy proposal.
Resource supply (creative, survival, externally supplied, or unknown) and
construction staging (single-pass, staged, or unknown) are independent policy
axes, so a survival build may also be staged. Creative mode grants unbounded
resources only when the proposal also names a disposable sandbox and
explicitly enables the grant.

`BuildPolicy@1` retains resource availability and pre-topology demand ranges,
first-class assumptions, protected-block rules, budget limits, staging
assumptions, constructability constraints, authority, and evidence. Site
protected cells become a referenced do-not-replace rule without copying or
choosing geometry. Missing resources, uncertain sufficiency, unit conflicts,
and proven shortages create obligations.

The resource compiler never chooses a block substitute, palette, topology,
geometry, construction sequence, or candidate. Staging records are supplied
project assumptions rather than framework phases. Unknown resources remain
unknown unless the explicit creative disposable-sandbox policy grants
unbounded availability.

`probe_loader.py` is the generic data-only probe caller. It accepts exactly one
`RawProjectRequest@1` plus optional `AuthorizedMaterialInput@1` records whose
immutable object digests verify. Executables, nested inputs, derived schemas,
cross-project references, and repository-level `.runs` fallbacks fail closed.
Compiled briefs and receipts persist only through the injected project
repository into the named run's `records/`; generated values never move back
into `input/`.

## Stage-wise operational Markov state

`operational_transition.py` prevents a pending tool plan from becoming a fact
used by later design decisions. Each Architect proposal binds the exact
candidate-working state and plan digest. Only a verified deterministic compile
or post-action observation can produce the next `OperationalMarkovState@1`.

The state separates `epoch` from `material_revision`. A pre-write failure may
advance the knowledge/obligation epoch while preserving every material fact;
a verified material change advances both. This lets a site failure become
expert-discovery input without pretending that grading, foundations, or any
other geometry already exists.

Experts are rediscovered from the obligations in each new state. The runtime
does not prescribe a site/foundation/program/circulation sequence; those names
appear only in fixtures selected by an Architect. A verified no-op is bounded
as `no_progress`, stale or pending results fail closed, and the compatibility
trace compiler returns a pure repository-ready payload. Its loader
deterministically replays the reducer from each proposal and observation; this
module no longer creates directories or writes an independent trace file.

The operational trace remains candidate-working evidence only. It has no MCP
execution handle, hard-usability verdict, aesthetic winner, or canonical
committer. Canonical promotion remains a separate final acceptance boundary.

## Design option portfolio and lineage

`branch_portfolio.py` persists immutable `DesignOptionPortfolio@1` snapshots
through the generic project repository. P023 schematic options become
independent active branches whose exact proposal, requirements, evidence, and
derivation references remain project data. Forks and revisions name their
exact parent revision; a combination must retain every parent requirement and
derivation reference or fail closed.

Park, reject, and select are explicit lifecycle transitions with rationale and
decision evidence. Selection additionally requires a named authority from the
project-supplied `SelectionPolicy@1`; branch registration order has no ranking
meaning. An existing selection must be explicitly released before another
branch can be selected.

P007/Pareto comparison remains a detached read-only observation bound to the
specific historical branch revisions it compared. It cannot change lifecycle,
delete an option, waive a hard gate, or create an automatic winner.
`SelectedSchematicBranch@1` is only an exact-base handoff to P040: it states
which schematic revision may enter design-development coordination, while
remaining explicitly not developed, hard-approved, candidate-built, or
canonically committed. Equal-depth divergent archives are reported as
ambiguous instead of choosing the first registered record.

## Durable design-development checkpoints

`development_controller.py` persists `DevelopedDesignState@1` only through the
P036 project repository. A checkpoint names its monotonic index, exact
predecessor state digest, project/run/base identity, selected schematic, and
complete coordination state. Re-saving the same checkpoint is idempotent;
another state at the same index, a missing predecessor, or an ambiguous latest
checkpoint fails closed.

The checkpoint contains detached advice, Architect decisions, conflict
resolutions, developed components, obligations, dependency edges, transition
receipts, and schematic invalidation evidence. It has no mutable latest pointer
and no separate framework output directory. Reload can continue coordination
or compile a dependency-local invalidation without a chat transcript, while
project `HEAD`, candidate state, MCP, and external worlds remain untouched.

## Developed-design candidate assembly

`candidate_assembly.py` accepts only a coordinated, non-invalidated P040
design-development state. It does not create a second candidate-program value
table. The executable plan binds each JSON leaf directly to the selected
schematic, stable `design-component:*` identities, developed-component records,
or explicit evidence, while the candidate archive embeds the exact
`DevelopedDesignState@1` snapshot it consumed.

The executable MCP payload remains project-authored. Every scalar or empty
container in that payload has a JSON-pointer provenance binding, and the
preview and execute receipts must name the same payload digest. MCP success
adds an artifact only to a new speculative `CandidateSubmission`; it supplies
no hard-usability, commitment-monitor, aesthetic-winner, approval, or
canonical-write verdict.

Build and approval policies are mandatory exact bindings, but candidate
assembly never fabricates an approval receipt. Validation-specific views must
be derived from named project records at their own boundary; there is no
reverse compiler from validator inputs into generation state.

Rejected and revised candidates may be stored as full immutable derivation
archives through the generic P036 run-candidate destination. Reload verifies
the complete design state, plan, policy, submission, and execution bindings
while project `HEAD` remains unchanged. Independent hard gates, commitment
monitoring, read-only aesthetic observations, approval, and the P008
single-writer committer still run downstream.

## Neutral geometry compilation

`geometry_compiler.py` deterministically checks a project-authored
`GeometryProgramProposal@2` against the exact `DevelopedDesignState@1`. Every
operation output must have exactly one evidence- or commitment-backed semantic
binding whose `component_id` exists in the selected component tree. Coordinate
frames and object inputs form acyclic dependency graphs;
the compiler derives one deterministic operation order and content digest for
every stable object.

On revision, a changed input, frame, or semantic binding invalidates its
dependent operation until that operation explicitly responds. Every changed
stable object must also name its exact predecessor digest, and disappearance
requires an explicit retirement. Unrelated object digests remain stable.

Missing assets, unresolved inputs, cycles, stale preconditions, invalid hosted
cuts, and hidden dependency changes return detached rejection receipts.
Content substitution is accepted only through a named lossy-substitution
receipt whose requested and replacement digests are both explicit. Successful
compilation still supplies no geometry executor, external-tool handle,
hard-usability verdict, or canonical writer.

## Player authority and reversible controls

`player_control.py` is a UI-neutral boundary for candidate preview, approval,
revision, pause, cancel, exact undo evidence, material/impact inspection, and
human preference. It does not implement a Minecraft screen or another
game-specific interface.

A P043 `AuthorityDecisionReceipt` may prove the identity of a named human, but
its explicit lack of candidate-approval authority is preserved. Candidate
approval therefore receives a separate exact-candidate, exact-plan, exact-base,
policy-bound and expiring receipt. A revised assembly always has a new plan and
submission identity, so the prior approval fails closed. Autonomous execution
is possible only when both the bound approval policy and the exact
`BuildPolicy@1` explicitly authorize a disposable sandbox.

Preview records expose the named world, bounds, additions/removals, collisions,
resource availability and shortages, protected-object warnings, and unresolved
obligations before any world write. Pause and cancel are immutable controller
states. Undo is never performed by this module: it recognizes only an M003
world-mutation trace whose exact plan, base, workspace, world and server match,
and reports `restored` only after exact-token compensation is acknowledged.

Player option, material, or aesthetic preference remains a read-only receipt.
Promotion readiness still requires an independent passing hard-validation
receipt, a completion-boundary commitment-monitor receipt, and valid candidate
approval. The result is only a handoff readiness record and carries no
canonical-write authority.

## External world mutation recovery

`world_recovery.py` verifies the M003 workspace journal as an exact, hash-linked
prepare/execute/observe/validate/compensate/finalize trace. Execute-started is
not execute-acknowledged: after a crash, an unknown call remains an uncertain
world write and cannot be simplified to an unchanged world.

Restart reconciliation compares the verified trace with the current exact
project `HEAD`, durable candidate record, candidate submission, and optional
commit receipt. It reports pending, orphaned, compensated, compensation-failed,
manual, or receipt-bound committed outcomes without calling a world tool or
writing canonical state. A missing canonical digest, changed head, uncertain
world, or mismatched commit remains manual reconciliation.

Compensation acknowledgement is evidence, not distributed rollback. The
runtime explicitly denies cross-system atomicity, world-write authority, and
canonical-write authority. Full recovery archives use only the generic P036
run-recovery destination and remain reloadable after process restart.

## Nested operational design controller

`design_controller.py` coordinates one bounded turn over a five-level design
state tree: global concept, phase, discipline, component, and parameter/detail.
It compiles only the root-to-target context needed for the current decision;
unrelated sibling state and raw history remain outside the Agent context.
Global commitments, active obligations, explicit interfaces, evidence, and the
target node's narrowed authority remain visible.

Expert discovery is recomputed from current obligations inside the current
P039 phase. The primary Architect may choose any valid subset and order. Expert
receipts stay detached, and the Architect must cite which advice was adopted or
rejected plus a trade-off rationale before proposing an exact-base
`DecisionOperator`. The controller does not rank conflicting advice, choose a
building answer, invoke a model or MCP, waive a hard gate, or write canonical
state.

Every accepted local proposal goes through P041 deterministic closure.
Only interfaces whose named source references changed may invalidate or reopen
other nodes. Repeated substantive plans and iteration exhaustion produce typed,
reloadable stop states. Missing authority produces a P043 exact-base
clarification pause; a named authority receipt resumes only through the
deterministic clarification compiler.

Phase progression is also compiled rather than announced by an Agent. All
active obligations must close and the exact-base P039 deliverable gate must
revalidate before the tree moves to the immediate next phase. A backward
revision recompiles the active phase projection, invalidates only the typed
deliverable dependency closure, and attaches its repair obligations to the
relevant local node.

Mid-run requirements enter through a verified P018 event chain. A compatible
interpretation becomes a proposed, non-active commitment plus a confirmation
obligation. Ambiguous interpretations stay blocked for authority selection. A
conflict with an active lock preserves that commitment, adds an explicit
authorized-revision obligation, and reopens only interfaces that cite its
scope. No new chat text may overwrite the current state directly.

`DesignControllerCheckpoint@1` is a typed projection and serialization
boundary, not a second durable state authority.
`ProjectControllerArchiveAdapter` implements the P018 event-store port over one
P036 run/branch record area, then stores the derived checkpoint beside that
immutable event chain. Every checkpoint binds the exact project/run/branch,
semantic run-base digest, complete event prefix, event-head digest, and
checkpoint digest. Reload derives the latest unambiguous checkpoint from the
repository records; there is no mutable `latest` pointer or standalone
controller directory. Project `HEAD` is unchanged until the separate accepted
candidate commit boundary advances it.

## Primary Architect model boundary

`primary_architect.py` connects an asynchronous provider to the nested
controller through two bounded proposal calls. The first call receives the
current `ContextSlice` and dynamically discovered read-only capability ids,
then proposes a subset and order. The validated capabilities run through the
existing registry. The second call receives the same exact context plus their
detached receipts and proposes one `DecisionOperator@2`.

The model never receives a canonical writer, workspace path, raw event history,
or world handle. Its capability selection and action remain proposals:
selection is checked against current discovery, advice must be adopted or
rejected, global hard commitments and current work must be cited, and only
`apply_architect_action()` may compile the exact-base operator.

At most two provider calls occur per turn. Timeout, provider exit, malformed
schema, byte/token budget exhaustion, unknown capability, invalid operator,
repeated plan, and controller budget exhaustion remain typed and reloadable.
Provider failure preserves the input checkpoint. P029 proves the offline
contract; a real invocation requires the explicit opt-in smoke and does not
claim architectural Gold.

The temporary production-facing route may use
`create_codex_cli_model_provider()` instead of an API adapter. It is isolated,
ephemeral, read-only, and proposal-only. API migration is an adapter
substitution behind `AsyncModelProvider`, not a controller rewrite.

## Append-only event history and state reconstruction

P018 separates justification history from current decision state.
`event_log.py` defines immutable `DesignEvent@1` records linked by sequence and
content hash. Each event binds actor and authority, exact prior state,
proposed canonical mutation, evidence and artifact references, validation and
commit receipts, reducer version, and resulting state digest. The module has no
default directory or filesystem writer: a project-owned store must be injected.

`state_reducer.py` deterministically rebuilds `CanonicalState@1`. Accepted
events advance exactly one version; rejected candidates and observed external
failures remain in the trace while preserving the same canonical reference.
Commitment creation and lifecycle changes use typed mutations and the existing
authority checks. Revisions preserve predecessor/successor lineage instead of
rewriting the earlier requirement. A commitment's future-facing
`monitor_state_ref` survives reconstruction while the larger event history
remains external to the current state.

The hash chain detects changed, omitted, or reordered records, and canonical
content is checked against its durable state digest. This proves state
reconstruction only. Referenced MCP/Rhino/Minecraft receipts are not executed,
so an intact log is not evidence that an external world has been replayed.
