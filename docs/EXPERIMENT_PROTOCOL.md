# ArchFlow experiment protocol

P062 tests a narrow claim: whether the current semantic–geometry production
mechanism behaves consistently across distinct building projects, and how named
mechanisms affect observable outcomes. It does not claim that architectural
design itself is Markovian, that three projects represent architecture in
general, or that passing a deterministic contract equals model performance.

## Authority and ownership

- Each building case is a separate P036 project. Its prompt, brief, evidence,
  component tree, geometry, criteria, family choices, and derivation trace stay
  with that project.
- The study project retains only preregistration, exact logical references,
  attempt intents, detached receipt bindings, outcomes, and derived tables.
- P053 remains the only production-provider authority. Experiment records do
  not invoke or replace a provider and never authorize fallback.
- P060 remains architectural-usability authority. P062 can report its status
  but cannot reinterpret a failed or unknown receipt as accepted.
- `SpatialOptionProposal.components` remains the only building ownership tree.

## Lifecycle

1. **Planned** — the immutable preregistration contains an assignment, but no
   attempt intent exists.
2. **Running/interrupted** — a P036-retained `ExperimentAttemptIntent@1` exists,
   but no matching terminal receipt exists. This is not a result.
3. **Completed or rejected** — a receipt binds successful provider evidence and
   exact terminal project records. A rejected pipeline is still an observed
   outcome, not a successful building.
4. **Provider failed or timed out** — a receipt contains the exact failed P053
   evidence, no terminal candidate, no fallback, and no fabricated metrics.
5. **Measured** — an outcome reports every preregistered metric. Missing values
   remain typed unknowns and make required comparisons ineligible.

The P036-derived result index adds no state transition. It reconstructs each
assignment from exact intent, receipt, and outcome record bindings. A terminal
receipt without an outcome remains `terminal_unmeasured`; planned and running
assignments keep the evidence-table readiness gate closed. A completed attempt
cannot be retried, which prevents repeated successful runs from being sampled
until a favorable result appears.

Retries are new preregistered attempts. Attempt index `n+1` must cite the exact
receipt digest of attempt `n`; the protocol never loops mechanically.

## Conditions

The study uses one shared provider profile so model, provider version,
fingerprint, budgets, timeout, and sampling configuration cannot drift between
conditions.

- **Full** executes the current production chain.
- **Generation-context ablation** withholds named context only inside an
  isolated experimental request boundary. It cannot change the production
  implementation or restore a quarantined generator.
- **Validation ablation** reuses the exact full-condition candidate and omits a
  named evaluator. It performs no provider replay and cannot turn artifact
  presence into architectural usability.

Every condition freezes terminal requirements as role, source schema, and
allowed-status records. Completion requires the exact set; a friendly role
label attached to another schema cannot satisfy it. Generation conditions keep
the full authority chain unchanged. Validation conditions may remove only the
named evaluator requirements and must reuse all remaining bindings exactly.

The first executable generation ablation is
`program-relationship-context`. For each case, P036 retains the full
`ProductionAuthoringContext@1`, a second typed context with only
`DesignProgram.relationships` removed, and an exact ablation receipt. The
derived context keeps nodes, ranges, scenarios, site, state tree, maturity,
phase gate, and build policy unchanged except for the build policy's required
program-digest rebind. It has no provider or generation authority and still
executes through P053/P056.
The assignment adapter resolves the context by exact P036 logical reference.
It verifies the ablation receipt and reconstructed context before returning the
derived record, and refuses to route validation ablations to a provider. The
ordinary single-context auto-loader is therefore not used for experiment cases.

For the current full chain, matching schemas are still insufficient. The case
adapter cross-checks the P056 lifecycle, sandbox, P060 usability, P061 family
compilation, and P061 family realization against one geometry program, design
state, component tree, scene, sandbox receipt, and family set. An empty family
set is retained if produced but cannot count as completed P061 evidence in this
study.

The complete case–condition matrix is frozen before execution. Conditions may
fail, but assignments cannot disappear after an unfavorable result.

## Executable persistence boundary

`python tools/run_experiment.py` supports six ordered P036 operations:
`preregister`, `intent`, `receipt`, `outcome`, `index`, and `verify`. Payload
commands accept typed JSON and print the exact retained project URI.
`preregister`, `receipt`, and `outcome` additionally require one or more explicit
`--source-project PROJECT_ID=ROOT` arguments; the label must match that P036
manifest and every embedded digest is reloaded before persistence.

Before preregistration becomes durable, every case must be a project distinct
from the study project. Its exact run/base, raw request URI and digest, and each
named input record must already reload from its declared INPUT or run-record
area. A path-shaped but absent logical reference therefore cannot freeze a
fictional case into the experiment matrix.

This adapter intentionally has no model-call authority. A full or
generation-ablation assignment is started by persisting its intent, executed
through the existing `archflow-runtime run-project` P053/P056 boundary, and
closed only after its case repository contains the preregistered terminal
chain. Validation ablations reuse the named source attempt without another
provider invocation.

## Measurements

Metric identifiers and methods are preregistered rather than hardcoded as
building answers. The P062 study will include terminal completion,
semantic–geometry consistency, P060 architectural usability, retry/repair
count, reload equivalence, and wall-clock/model-use measurements. Every value
must cite exact P036 evidence. No aggregate winner or aesthetic scalar is
produced.

## Claim boundary

Contract tests prove schema and lifecycle behavior only. They are not empirical
results. Paper tables may be generated only from completed P036 outcomes and
must report failures, unknowns, exclusions, sample size, provider identity, and
the limited generalization boundary.

## Current preparation boundary — 2026-08-18

Three promoted, non-synthetic case envelopes now exist at
`probes/p062-clinic-case`, `probes/p062-workshop-case`, and
`probes/p062-courtyard-case`. Each has one immutable raw request and one exact
`experiment-001` run containing a compiled brief, program, neutral site,
creative disposable-sandbox build policy, production authoring context,
project-authorized evaluation brief, and family-selection rule. Their program
and context digests are pairwise distinct. No case contains geometry,
provider, terminal-chain, attempt, outcome, or result-index evidence yet.

Each case also retains the typed `program-relationship-context` variant and
its receipt. This proves the ablation input can be reloaded; it does not prove
that either full or ablated context has been sent to a model.

`probes/p062-experiment-study` now retains the immutable three-case,
three-condition preregistration, dirty-worktree content identity, exact Codex
CLI provider profile, thirteen metric definitions, and an initial planned-only
index for all nine assignments. That index has sample size zero and is not
evidence-table ready. At registration time it contained no attempt intent,
provider call, terminal receipt, outcome, or empirical claim.

The first clinic intent exposed a pre-provider input defect: its frozen context
had no current required commitment, so P056 rejected it before any model
invocation. The study retains that intent, a running index snapshot, and a
typed diagnostic explicitly recording zero provider calls and no empirical
result. It is not counted as an experiment outcome. Each case now also retains
a corrected, project-derived context whose hard commitment comes from that
case's brief/evaluation evidence, plus a deterministically rebound phase gate,
matching ablation context, and passing preflight receipt. A successor
preregistration in `study-002` now freezes only those exact ready refs.
`study-001` is retained as superseded-before-provider rather than rewritten or
counted as a failed model run.

The first `study-002` clinic/full assignment reached P053 with the exact frozen
profile, then failed during Codex CLI process creation with
`PermissionError: [WinError 5]`. Its 14 ms offline receipt, failed P056 attempt,
experiment attempt receipt, and thirteen explicitly unknown metrics are all
retained with no fallback and no retry. The current index therefore contains
one failed assignment, eight planned assignments, sample size zero, and no
evidence table. This is execution-environment failure evidence, not a model or
building result.
The retained diagnosis identifies Windows command resolution as the boundary:
the logical `codex` name selected an extensionless npm shim that cannot be
started by `create_subprocess_exec`, while a process-only `codex.cmd --version`
check exits successfully as `codex-cli 0.145.0`. That diagnostic did not invoke
a model. Any corrected executable must be frozen as a new provider profile and
successor study rather than retroactively changing or retrying this assignment.

`study-003` froze that corrected profile with `codex.cmd` and performed one
new clinic/full invocation. Process creation succeeded and P053 reached the
real CLI, but the invocation returned `model.timeout`: the configured deadline
was 120,000 ms and the retained observed wall-clock was 167,109 ms because
post-timeout Windows process cleanup continued after the model deadline. The
frozen v3 experiment binder incorrectly treated the configured deadline as a
maximum observed duration, so it rejected the timeout binding before an
experiment attempt receipt or outcome could be written. The provider failure,
P053 envelope, binding-rejection receipt, and supersession remain retained;
no fallback or retry occurred and no model/building result is claimed.

The corrected protocol distinguishes the configured model deadline from the
observed invocation wall-clock. A timeout receipt may include bounded cleanup
overrun, but it still cannot exceed the enclosing experiment-attempt duration;
non-timeout receipts remain capped by the frozen provider deadline. `study-004`
freezes this exact code/contract correction and the unchanged `codex.cmd`
provider profile as nine planned assignments. It has no attempt intent or
provider invocation. A further live call therefore requires a deliberate new
execution decision rather than being an automatic retry.

`study-004` retains the authorization boundary and the user's subsequent
bounded live-execution authorization. The authorization permits preregistered
current-Goal calls under an immutable provider profile up to 300,000 ms; it
does not permit fallback, mechanical retry, external mutation, or invented
results. Its earlier v1 request, which mislabeled the context digest as an
input digest, remains explicitly superseded by the corrected v2 request.

`study-005` froze that 300,000 ms profile and issued one clinic/full intent.
The provider returned successfully, after which deterministic spatial
authoring rejected the proposal. The historical runtime could not persist this
combination because its failed-attempt receipt incorrectly required a failed
provider envelope. The process-local envelope was lost, so the study retains a
diagnostic-only P036 record: provider invocation and observed success are true,
receipt reconstruction and terminal evidence are false, and no empirical or
building result is claimed.

M053 makes this boundary durable. A successful P053 envelope followed by
deterministic semantic, spatial, or geometry rejection is a failed production
attempt with no checkpoint. P062 may bind it as `pipeline_rejected`, retain zero
or partial terminal evidence, and emit only ineligible typed-unknown metrics.
`study-006` freezes the repaired source and contract identities, the unchanged
authorized provider profile, and the same nine-assignment matrix before its
first call.

That first `study-006` clinic/full call is now closed as a measured failure.
P053 returned success after 182,672 ms and the complete envelope is durable,
but deterministic semantic-spatial parsing rejected the candidate. The study
therefore retains `pipeline_rejected`, no terminal building evidence, thirteen
typed-unknown observations, and an index of one failed/eight planned with
sample size zero. Inspection and deterministic replay of the retained output
show that its component tree, massing volumes, zones, and relationship
responses were present; the failing literal was
`hard_usability_verdict=false`. The parser correctly requires `null` before an
authorized evaluator has issued a verdict.

M054 treats that mismatch as a machine-contract ergonomics defect, not as a
reason to normalize model output. The model-facing contract now publishes the
exact values `proposal_only=true`, `selected=false`,
`hard_usability_verdict=null`, `design_development_complete=false`, and
`execution_ready=false`. Production also retains the underlying typed
rejection code and bounded diagnostic instead of collapsing it into a generic
compilation error. `study-006` remains immutable; any next invocation must use
a successor preregistration whose source manifest includes this contract file.

`study-007` supplied that successor identity and performed one new clinic/full
call. Its provider succeeded after 177,609 ms and respected the five exact
authority literals, including `hard_usability_verdict=null`. Strict parsing
then reached the next previously implicit boundary and rejected the nested
grid object with `SpatialProposalError: spatial grid basis schema drifted`.
The provider envelope, exact typed diagnostic, failed attempt, unknown outcome,
and one-failed/eight-planned index are all durable; no building is claimed.

M055 closes the systemic issue instead of adding a project-specific example.
`SemanticSpatialAuthoringContract@2` publishes exact generic fields and types
for grid basis, footprint cells, levels, massing volumes and bounds, zones,
components, connections, and constraint responses. Component maturity and
response-status enums are explicit, and local ids remain distinct from logical
refs. A serializer-backed regression guards contract drift. No clinic geometry,
typology, or parameter is encoded in the framework, and malformed output is
still rejected rather than repaired.

`study-008` froze the recursive contract digest and issued one new clinic/full
call. P053 succeeded after 187,155 ms and the proposal passed recursive JSON
parsing. Deterministic validation then rejected it as
`spatial_authoring.source_rejected`: the model placed program-node and
relationship refs in proposal `evidence_refs`. Those refs are legitimate
response targets, but the current compiler only accepts state/program/site/
policy provenance refs as proposal evidence. The exact envelope, diagnostic,
failed attempt, unknown outcome, and one-failed/eight-planned index are durable.

M056 removes that remaining ambiguity with one shared compiled contract.
`SpatialAuthoringReferenceContract@1` publishes exact allowed evidence refs,
allowed and required response refs, and allowed expert-advice refs inside the
project-specific invocation. Deterministic validation consumes the same object,
so prompt and gate cannot drift. Nested sources must remain proposal-evidence
subsets, and an unknown reference cannot self-authorize by appearing in both
expert-advice and evidence lists. Concrete refs remain in provider receipts and
project records, never in framework defaults.

`study-009` froze that reference contract and issued one new clinic/full call.
P053 succeeded after 194,890 ms, and the proposal passed strict recursive and
reference validation. The next exact gate rejected it because a massing
volume's inclusive X/Z projection was outside the declared footprint cells.
The provider chose 48 cells at 25 square metres each for a 1,200-square-metre
area, but its inclusive volume bounds covered a larger discrete union. This
failed output remains immutable, with no normalization or building claim.

M057 publishes the remaining generic deterministic rules as
`SpatialAuthoringValidationContract@1`: bounds are inclusive; footprint area is
cell count times area per cell; all volume X/Z projection cells must be in the
footprint; levels and volumes stay inside the site envelope; named levels are
vertically contained; each current function enters exactly one zone;
connections match relationship endpoints and direction; and every required
response is covered. Current envelope, footprint ranges, function refs, and
relationship facts are compiled into the invocation, while no project answer
is stored in framework code.

`study-010` froze that validation contract and advanced materially farther.
Two independent semantic-spatial calls passed the unchanged deterministic gate,
and a third successful P053 call selected a valid supplied option. The three
receipts total 545,969 ms of observed attempt wall-clock. Selection parsing
still failed because the response included `selected_proposal_digest` instead
of the required `exact_option_set_digest`. The study is therefore retained as
`pipeline_rejected`, not completed, despite containing two accepted proposals.

M058 publishes `SchematicOptionSelectionContract@1` with exactly four output
fields, the fixed output schema, exact current option-set digest, and allowed
option ids. It also preserves exact missing and extra field names in rejection
codes. The model may choose and justify only; supplied geometry and validation
results remain immutable, and no selection response gains persistence or
canonical-write authority.

`study-011` froze that selection contract and retained two successful
semantic-spatial invocations followed by a real `model.timeout` during
schematic selection. The provider receipts report 185,139 ms, 258,984 ms, and
342,157 ms respectively; the last call crossed its 300-second configured
deadline. The attempt is therefore `timed_out`, not a pipeline success, and it
contributes no terminal building or comparison sample.

M059 introduces `SchematicOptionDecisionProjection@1` only at the detached
choice boundary. It binds each original option id, option digest, and proposal
digest while publishing the functional organization, massing, topology,
components, zones, connections, constraint-response risks, and tradeoff text
needed for a decision. It omits repeated provenance and execution-authority
material. The projection cannot be reloaded as an option, and deterministic
code still recovers the untouched member of the exact validated option set.

`study-012` froze that projection contract but stopped on its first semantic-
spatial response. The P053 call succeeded in 199,500 ms; unchanged validation
then emitted `spatial_authoring.source_rejected` because the proposal cited
evidence absent from the current state. The 199,662 ms attempt remains
`pipeline_rejected`, with no terminal building and no comparison sample.

M060 permits one internal semantic-spatial repair only after a bound successful
provider output is deterministically rejected. The second request carries the
exact rejected output and digest, prior request and receipt identity, stable
error code and diagnostic, unchanged base, reference, validation, and output
contracts, and an instruction to return a complete replacement. Framework code
does not patch fields. Provider failure and request-binding mismatch do not
enter this path. Rejected and accepted authoring receipts remain separate P036
records, and only an accepted typed option enters the option set.

`study-013` froze the repair policy and received two individually accepted
semantic-spatial outputs in 189,469 ms and 196,312 ms. Both independently chose
the id `clinic-linear-sequence-01`; option-set construction then raised
`SpatialProposalError: option ids contains duplicates` outside the historical
typed production-failure boundary. The individual authoring and embedded model
receipts are durable, but their P053 authority envelopes were process-local and
were not reconstructed. Consequently this study has a diagnostic and
supersession only, with no experiment attempt, outcome, building, or empirical
comparison claim.

M061 adds `SpatialAlternativeAuthoringContext@1` to every second-option request.
It carries the exact excluded option id, option digest, topology signature, and
non-authoritative decision projection, and requests a complete independent
alternative. Framework code does not rename or perturb provider output. Any
remaining duplicate identity, digest, or topology conflict becomes the stable
typed code `spatial_authoring.option_set_rejected` while the production runtime
still holds the exact P053 envelopes required for P036 failure archival.

The case records deliberately leave topology, `SpatialOptionProposal.components`,
geometry, material intent, and selected family identity absent. This is input
readiness, not a model run and not evidence that ArchFlow generated or validated
three buildings.

M062 retains architectural-revision studies 016–023 as negative production
evidence rather than silently repairing provider output. Study 016 reached a
compiler-accepted geometry proposal only by omitting a newly changed component,
then failed the P055 lifecycle. The framework now rejects that omission before
compiler acceptance. Studies 017 and 018 timed out after publishing exact
changed-component and predecessor-revision contracts. Study 019 completed four
successful provider calls, but exhausted bounded geometry repair because the
model still failed to apply the supplied exact revision token and changed
unrelated predecessor objects. These records establish the current
provider/profile boundary only; they are not a usable-building result or a
P060/P061 terminal sample.

Studies 021 and 022 reached durable semantic-geometry production and sandbox
realization, then failed only the exact project-derived usable-entry criterion.
Study 022 showed that the first geometry round had authored the required typed
door assembly, while its replacement repair round fixed compiler tokens but
dropped that assembly because only issue text was carried forward.
`GeometryProposalRepairContext@1` closes that generic context-loss defect by
including the exact rejected output and digest in the next request. The model
must still return one complete replacement over the original predecessor; the
framework does not merge rounds or patch geometry. Study 023 froze the changed
contract but timed out during its first semantic call, before geometry repair
could be evaluated. Study 024 records that a 600-second retry profile cannot be
preregistered because the completed provider specification caps timeout at 300
seconds. Neither record is a usable-building result.

Study 025 isolates the missing recovery step instead of replaying the entire
production path. It reloads study 022's exact rejected geometry-round receipt
through P036 and makes one preregistered real provider call with no fallback.
The provider returned success in 181,125 ms; the replacement preserved the
model-authored door assembly, compiled, and produced a sandbox scene with one
opening object. The study outcome deliberately keeps P055 lifecycle,
architectural usability, and family-terminal claims false. It therefore proves
durable rejected-round context and realized repair, not the full M062 terminal
or a usable-building result.

Study 026 performs no provider replay. It compiles study 025's accepted repair
through the exact-base P055 lifecycle, checkpoints the lifecycle and retained
P053 envelope through P036, realizes that checkpointed program again, and
recomputes all P060 measurements from current project and scene records. The
four mandatory clinic criteria pass with values `1.0`, `1.0`, `true`, and one
usable opening. This is the M062 terminal result for the clinic revision chain;
the outcome explicitly leaves the family-terminal flag false and is not a
substitute for P062's preregistered multi-building comparison.

`study-027` is the new preregistered multi-building matrix after M062 closure.
It preserves the same three case projects, full/generation-context/validation
conditions, thirteen metrics, single-attempt rule, and Agent CLI profile while
freezing the current code and extended repair contract identities. The study
ceiling is explicitly 14,400,000 ms (four hours). Its clinic/full intent is
retained as running, with eight assignments planned and no provider receipt or
outcome yet; it must not be counted in the evidence table until an exact attempt
receipt and measured outcome exist.

`study-028` supersedes the interrupted `study-027` clinic intent rather than
resuming it. That intent was issued 2026-08-18 and no provider was ever
called; the preregistered wall-clock metric runs from intent issue to
terminal receipt, so a nine-day-old intent cannot honestly satisfy the
four-hour study ceiling. A typed supersession record retains this decision
in the study-027 run, the frozen matrix and profile continue unchanged, and
the previously dirty code identity is committed rather than re-frozen.

On 2026-08-28 study-028 executed the complete preregistered nine-assignment
matrix and reached the first table-ready P062 index. Every full and
generation-ablation attempt made exactly four successful P053 Codex calls
(one hundred thirty-two to two hundred sixty-five seconds of provider time)
with zero provider failures and zero bounded repairs. Outcomes are honest
and asymmetric:

- `courtyard/full` is the first fully completed live assignment in P062
  history: all four mandatory project-derived P060 criteria pass, one
  aggregate family compiles and realizes, and the assignment is
  comparison-eligible.
- `clinic/full` fails only `usable-main-entry` with zero opening objects —
  the same locality as study-014 — and `workshop/full` fails only
  `three-storey-organization` with two authored storeys against three.
  Both are retained as `pipeline_rejected` with all thirteen metrics.
- All three generation-context ablations degrade measurably: clinic
  relationship coverage drops from 1.0 to 0.0 with all geometry bound to
  the root component and no bindable family; workshop mandatory pass rate
  drops from 0.8 to 0.6; courtyard drops from 1.0 (completed) to 0.5.
  Withholding the program-relationship context therefore visibly removes
  relational structure from otherwise successful generation.
- All three validation ablations complete on the reduced terminal set while
  the withheld `architectural-usability` evaluator stays typed-unknown, so
  artifact presence never upgrades into architectural usability.

Two defects surfaced and are retained as corrections rather than rewrites:
the first clinic intent stamped an assumed clock roughly 195 minutes early
(a typed clock-discrepancy note binds the observed timestamps), and the
driver-authored observation-set records initially omitted a schema field
(schema-correction notes name each defective record and its identical
schema-bearing successor). The one comparable completed sample, six exact
failures, and two ablation-degradation observations enter the paper only
through this index and these receipts.

## P063 repair-locality studies — 2026-08-28

P063 measures repair locality over the retained study-028 full-condition
baselines with three deterministic, provider-free strategy executors. All
concrete deltas and gold impact sets are project-owned records proposed by
the executing harness before any episode ran, marked `annotator_is_harness`
and open to human architectural review; the framework owns only the generic
graph, delta, strategy, and metric contracts.

`repair-001` executed the frozen twenty-seven episode matrix and exposed an
executor defect: identity repair after a component replacement guessed the
replacement id instead of following the delta's own rename mapping, so one
retained reference could stay stale even under the dependency-scoped
strategy. `repair-002` fixed identity repair and added the
`no_new_validator_failures` metric (three baselines carry pre-existing
failed criteria, so absolute repair success conflates the edit with the
baseline), then exposed a second defect: the whole-chain recompute set was
derived only from the program lane and missed the component-geometry lane.
Both defective runs are retained unmodified as executor-defect evidence.

`repair-003` is the corrected frozen matrix. Its table-ready index shows
the preregistered asymmetry exactly:

- target-only patch: 0/9 episodes without new failures; every episode fails
  on a typed stale dependency; mean recompute ratio 0.018.
- whole-chain rebuild: 9/9 without new failures; mean recompute ratio 1.0;
  mean impact precision 0.181 against the gold closure.
- dependency-scoped repair: 9/9 without new failures; mean recompute ratio
  0.173; impact precision and recall both 1.0 in every episode; unaffected
  record retention 1.0.

Validity is therefore identical between whole-chain and dependency-scoped
(H1), dependency-scoped recomputes about a fifth of the derived state that
whole-chain rebuilds (H2), unaffected records and the baseline commitment
are fully retained (H3), and the dependency-scoped reopened set equals the
typed gold closure exactly while whole-chain over-reopens by more than five
times (H4). These are twenty-seven deterministic paired episodes over three
buildings, not a population claim, and no aggregate winner is written by
the framework.

## P064 golden inverse derivation — 2026-08-28

P064 transcribes the frozen V3 golden sample's retained nine-stage
generation trace into V4 records under the L8 quarantine: the three golden
files enter only as digest-pinned read-only external evidence, and every
transcribed component, decision, and dependency edge cites the exact trace
or manifest field it transcribes. Fifteen semantic components carry the
333 V3 rule outputs as family annotations; ten decisions and five typed
dependency edges make the retrospective V3 explanation chain operational
in the V4 form.

The coarse massing replay derives drum, dome, and oculus geometry only
from the transcribed canon ratios (span 94, rise/span 0.500, oculus/span
0.188) with no value fitted against the occupancy grid. Against the frozen
schematic (98 x 94 x 127; the parsed solid count 325,282 matches the
manifest exactly), the retained fidelity receipt reports plan-footprint
IoU 0.925, elevation-silhouette IoU 0.937, exact bounding-box agreement,
and an open crown oculus. The receipt explicitly denies byte-replay,
full-fidelity, and V3-execution claims: V3 reproduces its own golden
byte-for-byte, and the V4 claim is only that transcribed canon-ratio
massing agrees with the frozen envelope at the reported levels.
Full-fidelity replay and live-provider re-derivation remain out of scope.

## M065 + P065 monument-scale staged derivation — 2026-08-28

M065 makes repetition a first-class geometry citizen: beside the retained
linear array, a generic `radial_array` operation replicates one input
about an explicit axis and center by an explicit angle step, with exact
rotated union bounds, per-replica inverse-rotation voxel membership, and
fail-closed degenerate inputs. Neither operation carries any typology or
placement default.

P065 proves golden-complexity derivation through the formal runtime with a
scripted provider (explicitly not a live-model claim). From raw project
input, four provider calls compile the coarse half-scale monument
(portico, rotunda, dome at drum span 46 on a 49 x 64 site); three P055
exact-predecessor stages then deepen the same eleven component identities:
colonnade and axial entry; interior recess and aedicula rings with the
dome oculus; five radial coffer rings with an attic ornament ring.
Thirty-six authored operations expand through typed arrays into 308
realized instances, and the promoted probe retains 37,013 occupied voxel
cells at stage three with per-stage scenes, receipts, voxel views, and
artifact-presence validations.

Two digest-stability lessons are retained in the proof rather than worked
around: numeric parameters must canonicalise identically through the
authoring-output round trip, and revising a component reopens all of its
bound geometry — every operation of a revised component must acknowledge
its changed binding and carry an exact prior-digest precondition, with
acknowledgments kept cumulative across stages.

A machine-local fidelity receipt measures the derived envelope against the
frozen golden at the declared half scale with no value fitted to the
occupancy grid: plan-footprint IoU 0.865, elevation-silhouette IoU 0.949,
bounding box 48 x 47 x 62 against the downsampled 49 x 47 x 64. The
receipt denies reproduction and full-fidelity claims; a live-model
monument run remains a separately evidenced future card.

## P066 live root and the standard render set — 2026-08-28

The V3 golden render convention (`kevin-section-render-standard`) is
reimplemented as a V4 tool without importing V3 code: a fixed axonometric
camera with the entrance rotated toward the viewer, a transverse section
on the drum mid-plane and an axial section on the entrance mid-line each
keeping the far half so the cut face opens to the camera, and a front
orthographic elevation with depth dimming. The P065 monument's four
standard views are retained in the probe's export area under a
source-manifest record binding view digests to the exact stage-3 voxel
view; renders remain read-only visual evidence with no acceptance
authority.

P066 then opened the live lane: the same monument prompt and context, a
frozen Codex profile, and one preregistered root attempt. The live model
authored two independent monument options, a selection, and compiled
neutral geometry in four calls over 183 seconds with no repair round,
choosing a coarser two-square-metre grid basis itself to fit the frozen
output budget. The result is honest coarse massing — three components and
three solids at 7,800 voxel cells — exactly the root-step granularity the
scripted P058/P065 chains also start from. Live deepening stages remain
P066's open scope, with the study chronicle marking revision-stage
authoring as the known weakest point of live-model discipline.

## P067 web precedent retrieval and adoption — 2026-08-28

P066's first live root exposed the knowledge gap directly: with no
typology evidence in the authoring context, the model authored a flat
portico slab. P067 closes the gap with live web retrieval held three
gates from generation: a page is retained as a content-addressed snapshot
with no authority; candidate facts cite exact quotes with character spans
(harness-extracted and marked as such); and only a typed adoption under a
named authority compiles them into build-policy constraints whose
provenance chains constraint to adoption to snapshot to URL. Raw page
text never enters a prompt.

The paired live observation: from the same prompt and profile, the
attempt without precedent (live-001) authored three components, three
plain solids, and a flat portico roof; the attempt with three adopted
facts (live-002) answered every precedent constraint in a typed response,
added a dedicated pediment component, authored the pediment as a genuine
triangular-profile extrusion with a revolve dome, and honestly marked the
coffer-detail constraint as a risk at coarse maturity. The enriched
attempt also shrank its massing well below the program range — retained
as an honest attention trade-off observation, not repaired. This is the
canon analogue of the P062 relationship ablation: knowledge absent from
context does not emerge, and knowledge adopted into context does.

## P070 decision-scoped research loop — 2026-08-28

P067 proved retrieval can carry precedent into one generation; P070 makes
it a calibration instrument aimed at named decisions. A typed
`PrecedentQuery` must declare the declaration refs it calibrates before
any page is read; the retained snapshot is cut into bounded keyword
windows (never raw page text); one RESEARCH-phase invocation may output
only quoted candidates; the harness locates every quote verbatim in the
full snapshot and computes the authoritative character span itself, so
paraphrase and fabrication are typed per-candidate rejections; and a
typed adoption routes each surviving fact to exactly the decisions it
names, with a calibration record binding fact ids to declaration refs.

The live closure took three runs, all retained. research-001: the model
returned one candidate with empty decision refs and free-text enum values
— rejected. research-002: with the output contract tightened to name the
allowed enum values, the model honestly returned zero candidates — the
offset-arithmetic burden was the blocker. research-003: after moving span
authority into the harness (the model quotes; the machine locates), the
same page yielded three verbatim-located facts — the 10:1 slenderness
canon, the 6:5 total-to-shaft ratio, and capital-proportion flexibility —
adopted and calibrated onto the three column declarations. The failure
sequence is itself the finding: a research contract survives a live model
only when the machine owns every verification the model is bad at.

## P071 CAD realization equivalence — 2026-08-28

Voxels stay the coarse acceptance substrate; P071 gives the same compiled
program an exact-measurement realization. A deterministic translator maps
the promoted P065 monument program — 36 operations, 17 physical objects —
onto a rhinoscriptsyntax build script (regeneration is byte-identical),
executed in an external Rhino 8 instance through the MCP adapter: 308
breps, with drum and dome-shell booleans, lofted taper, and full radial
rings realized as true NURBS solids. An analytic bounds replay derived
from the program itself — not from either renderer — is compared against
the CAD-measured bounding boxes: equivalent at a maximum deviation of
0.346 m against the 0.6 m tolerance (the dome-cap facet of the 24-gon
loft), zero translation losses, receipt retained beside the sandbox
realization it mirrors. The adapter owns no design authority: curves and
transforms it cannot express arrive as typed losses, and the receipt
names the program record, script digest, measures digest, and adapter
identity.

## P072 semantic CAD emission — 2026-08-28

The CAD lane stopped flattening semantics: with zero plugins, the
translator now emits every physical object with its object id as the
Rhino object name, on a per-component nested layer with a deterministic
color, carrying binding ids, component id, commitment refs, evidence
refs, and producer op as key-value user text, with document-level user
strings anchoring the file to its exact program record. Component
repetition became native instancing — one block definition per family,
N transformed instances — so the 308-brep monument is now 13 families
plus four singletons, mirroring the family identity the program owns.
The equivalence receipt gained a semantic round-trip section: the build
script reads its own semantics back from the document and the receipt
compares them against an expectation derived from the program alone.
Monument rebuild: geometry still equivalent at 0.346 m maximum
deviation, semantics verified 17 of 17, the saved .3dm carrying
everything.

## P073 IFC semantic export — 2026-08-28

The same program now exports to the open building-model standard
through one library. Elements are named by object id and typed through
a caller-supplied component mapping (the framework ships no building
vocabulary; unmapped components become proxies); property sets retain
the full semantic account per element; families become one
representation map reused under N transformed IfcMappedItem instances —
the IFC-native form of the program's array identity. Geometry maps
exactly where IFC expresses it: rectangle and circle profile extrusions
for solids and constant-radius revolves, and the drum wall as a genuine
IfcBooleanResult chain with its door void applied. Lofts become faceted
breps from the exact ring vertices, and the dome-shell boolean keeps
its base representation with the inner and oculus voids recorded as a
typed partial-representation note — never silently approximated. GUIDs
derive from content keys and no timestamps are written, so the export
is bit-deterministic; validation re-reads the file and matches element
names, semantics, and mapped multiplicities against the program.

## P074 axial dependency and symmetry self-check — 2026-08-29

The user's eye caught what no gate measured: the monument's front
elevation was asymmetric. Measurement confirmed and extended the
finding on the retained scenes — the colonnade group sat +0.65 m off
the committed axis, the beam row +0.90 m (overhanging the portico roof
on one side), and the plinth +0.50 m, a defect the eye had missed. The
typed FAIL findings were persisted beside the old scenes, which were
never rewritten.

The repair is the dependency pattern, not a number patch. The primary
axis became a typed HARD commitment the runtime itself enforces — the
first compile attempt was rejected with "required commitment is absent
from current state" until the axis existed as a commitment with an
authority and evidence. Every semantic binding now answers it, so
revising the axis reopens the dependent geometry through the existing
closure; the row origins are derived center-out from the axis
(origin = axis − ((count−1)·step + width)/2), so the numbers cannot
drift because they are never stored; and the self-check compiles from
the same basis — a generic axial-symmetry measurement over realized
scene bounds, with axis, subjects, and tolerance supplied by the
project. One basis, three projections: derivation, reopening,
criterion.

The re-derived monument (fresh probe, four stages, 308 instances,
root selection digest identical to the original run — determinism
holding across the fix) measures exactly zero offset for all five
groups at every stage. The CAD rebuild stays equivalent at 0.346 m
maximum deviation with semantics verified, the IFC re-export validates,
and the front elevation is symmetric. The episode is the paper's
self-check argument in one page: a criterion absent means a defect
passes; a criterion compiled from the basis catches a quarter of a
metre.

## M075 criterion-gated stage acceptance — 2026-08-29

The user asked the right question about P074: why did the FAIL exist at
all — if the rules held, a failing model should never have been built.
The honest answer had two parts. Historically, the criterion was born
after the model: the FAIL findings over the old scenes are archaeology,
evidence of the gap, not a gate working. Structurally, even after P074
the criterion ran as a standalone tool — stage acceptance still
validated only artifact presence, so a failing realization could have
been archived ACCEPTED again tomorrow.

M075 closes the structural half. Realization is measurement substrate —
the model must be built in the workspace to be measured at all — but
acceptance is a separate act the criterion now guards: the symmetry
gate runs before any archive disposition, its record persists on both
outcomes, a failure writes REJECTED and raises a typed stage-gate
error, and a pass writes ACCEPTED citing the gate record that guarded
it. The fail-closed drill proves the invariant the user demanded: an
off-axis stage yields exactly one archive record, disposition REJECTED,
and no ACCEPTED record exists anywhere.

## P076 basis index and coverage view — 2026-08-29

Retrieval evidence became addressable by decision. A pure derivation
over the retained basis records shards the adoption store: one file per
decision ref, so editing one decision means reading exactly its shard —
bounding context tokens and blast radius together, the same philosophy
that keeps raw pages out of prompts now applied to the adoption store
itself. The identical derivation yields the coverage view (a decision
named by any query but backed by zero facts is a typed uncovered entry)
and the reverse source map (a revised snapshot names exactly the facts
and decisions it reopens). Built live over the five research runs:
five decision shards, three source shards, zero uncovered — the
colonnade-bay-spacing shard alone carries the four Palladian symmetry
facts a future layout edit would need, and nothing else. The index is
a derived view under index/basis/ with a manifest naming its source
records: rebuildable, authority-free, never a substitute for the
records. Bonus catch: archcheck's probe firewall flagged the migrated
CAD scripts as executable logic inside probes — renamed to .py.txt
with superseding manifests, digests still bound by their receipts.

## P068 declaration gates closed; consumption-side basis wiring — 2026-08-29

The declaration contract became a working gate with all three of its
remaining pieces. The site quadrant joined the vocabulary. Gate-passed
declarations of the caller-named irreversible quadrants now compile
into HARD commitments whose satisfaction criterion is the field itself
and whose evidence chains the gate receipt to the field's range
provenance — reopening a declared value is an authority-gated
commitment transition, not an edit. And the basis index gained its
consumer: the authoring prompt receives exactly the shards of the
contract's fields, nothing else — measured live at 6 of 15 facts and
3,661 of 17,388 characters injected, a 79 percent reduction over
store-wide injection, under a hard 20k-character prompt bound.

The live paired observation is a study in what a gate is for. The
ungated roots (live-002, live-003) had passed with a token-scale or
axis-confused massing. The gated run (live-004, a project-authored
seven-field white-model contract) put four codex attempts through the
same door and every one was a typed rejection, none repaired: an output
budget exhausted; an output field drift that exposed a real contract
conflict (the format contract and the declaration contract disagreed —
fixed so they cannot); a non-mapping declarations block that exposed a
real fail-open crash (now a typed declaration_rejected with a
regression test); then two honest content failures, a footprint outside
its cited range and a massing outside its footprint. The gate raised
the bar above what the low-effort profile clears — that cost of rigor
is the finding, and the repair-loop and effort escalation remain the
P066 lane's open work.

## P077 relation coverage and multi-source sweep — 2026-08-29

Dimension two became a number. Candidate dependency edges are now
enumerated mechanically — component pairs whose realized bounds meet
within a caller tolerance — and every candidate must resolve through a
machine-detected program edge, a declared edge with provenance, or
surface as a typed uncovered_relation. The symmetric monument's
stage-3 ledger reads 10 candidates, 3 resolved, 7 uncovered: thirty
percent coverage, with the uncovered list (coffer attachment, statuary
rings, aedicula, recesses, colonnade-rotunda junction) retained as the
open work queue rather than smoothed away.

The research driver learned to sweep. One query now snapshots and
invokes every source separately — span verification stays inside each
snapshot — and unions the surviving facts into a single adoption with
per-source honesty preserved (empty sources and failures retained as
rows, duplicate fact ids dropped with a note). The first live sweep
(rotunda wall construction, Pantheon page plus Roman concrete page)
adopted nine facts in one pass: the 6.4-metre wall thickness with its
eight barrel vaults, the 43.3-metre interior diameter corroborated
again, the aggregate grading from dense base to pumice crown attested
by both sources, relieving arches and hidden chambers, and the Roman
concrete composition the material channel will cite.
