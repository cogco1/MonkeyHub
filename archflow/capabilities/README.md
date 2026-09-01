# Capabilities

Owns the open registry used to describe, discover, and select available
capabilities at runtime. Entries may represent MCP tools, CLI commands, models,
or future capability types.

The registry describes contracts and availability; it does not create permanent
expert roles or impose invocation order. See the module map in
[`docs/DYNAMIC_MAP.md`](../../docs/DYNAMIC_MAP.md).

## State-responsive experts

`experts.py` adds an open registry for optional, read-only expert capabilities.
Discovery is recomputed from the current state's open-obligation topics and
the evidence kinds actually available. The initial vocabulary covers
program/use, circulation, and constructibility, but these are not stages:
registering another expert needs no scheduler change, and sorted discovery
order is only deterministic presentation order.

Every handler receives an immutable `ExpertSnapshot` containing copied program
JSON, obligation values, bounded evidence pointers, and the exact `StateRef`.
The interface deliberately exposes no live `CanonicalState`, committer, MCP
adapter, or world handle. Advice is therefore only a candidate artifact; it
cannot mutate canonical state or claim that a build is usable.

Invocation has declared time, output, and retry limits. Exceptions, timeouts,
oversized output, and missing evidence become bounded `ExpertReceipt` values.
Missing evidence produces no advice. A caller may continue without an optional
expert, while retaining the receipt for audit.

## Retrieval capabilities

`retrieval.py` registers readonly providers by id and topic. Discovery returns
a capability set, not an invocation schedule. The Primary Architect must name
the provider it intends to call; a missing id returns a failure receipt and
never falls back to another command. Retrieved excerpts remain hypotheses until
the brief and commitment compilers interpret and authorize them.

## Programming experts

`programming.py` projects the current `DesignBrief@1` into a detached
`ProgrammingSnapshot@1`. Only unresolved topics, exact-base identity, claim
references, constraint references, and bounded evidence are exposed. The
snapshot has no repository writer, scheduler, candidate selector, or geometry
tool.

Programming advice is a read-only proposal receipt. It may suggest
evidence-backed functions, ranges, assumptions, and relationship hypotheses,
but the deterministic P021 compiler must validate them before they enter
`DesignProgram@1`. Discovery follows current obligation topics; this module
does not encode a permanent programming expert or a fixed expert sequence.

## Constructability experts

`constructability.py` projects `BuildPolicy@1` into a detached exact-base
snapshot. It exposes current resource, staging, protection, budget, and
constructability obligations plus evidence references; it exposes no
inventory writer, world handle, MCP adapter, palette selector, or canonical
committer.

The snapshot is discovery input, not an invocation schedule. Advice remains a
read-only proposal receipt and cannot authorize a substitute material, waive a
shortage, select geometry, or claim that a candidate is constructible.

## Phase-bounded expert capabilities

`phase_gates.py` adds P039 maturity metadata around the open expert registry.
Discovery is the intersection of four current facts: the `D_v,k` maturity
phase, open-obligation topics, available evidence kinds, and each capability's
allowed phases. Missing phase metadata excludes a capability; it never grants
cross-phase authority by default.

Phase metadata may declare advisory deliverable roles only for phases in which
the capability is allowed. This lets an early expert warn about downstream
risk without producing or certifying a later-phase package. The deterministic
phase gate, not an expert assertion or model confidence, decides whether typed
prior-phase deliverables are complete.

Discovery remains a set, shown in stable id order for reproducibility. The
Primary Architect may select any unique subset and any within-phase order.
`validate_architect_selected_expert_order()` checks only membership and
preserves that chosen order; registration order is neither a schedule nor
design authority.

## Schematic spatial proposals

`spatial.py` compiles Architect-authored `SpatialOptionProposal@2` values; it
does not contain a building-type lookup, standard footprint, typology fallback,
room arrangement, material palette, or coordinate generator. Concrete levels,
footprint cells, massing volumes, function zones, topology links, typology
hypotheses, and palette references are proposal data from the current project.

Compilation requires the exact P022 operational state and a current P039 gate
from site/resource coordination into schematic design. The Program,
`SiteContext`, and `BuildPolicy` must belong to the same project, run, canonical
base, and brief; the policy must retain the exact program and site digests.
Function nodes enter exactly one schematic zone, program relationships retain
their endpoint and direction semantics, massing remains inside the observed
site envelope, and an explicit project-supplied grid basis connects footprint
cells to a cited program area range.

Hard upstream constraints and unresolved obligations must receive typed
schematic responses. A response may remain `risk`: structure, circulation,
envelope, material, and constructability coordination belongs to P040, so P023
must not pretend to resolve it. At least two structurally distinct options are
required and emitted in stable ID order solely for reproducibility. The option
set has no ranking, selection, hard-usability, design-development, execution,
world-write, or canonical-write authority.

## Semantic-spatial authoring

`semantic_spatial_authoring.py` places a typed provider boundary immediately
before the deterministic spatial compiler. One bounded request carries the
current operational digest, phase gate, program, site context, build policy,
and a machine-facing output contract. The provider must return the existing
`SpatialOptionProposal@2`; no parallel semantic record is introduced.

The proposal creates its `DesignComponent` tree and coarse massing volumes in
one object. Construction and compilation reject a missing or cyclic parent,
an unknown, multiply-owned, or unowned volume, an unknown source, a stale base,
and a mismatched provider receipt before the result can enter an option set.
The returned receipt is proposal-only and carries no selection, persistence,
validation, execution, or canonical-write authority. Agent CLI, a scripted
test provider, and a later API adapter therefore share one `AsyncModelProvider`
port without changing the domain contract.

## Design-development coordination

`design_development.py` consumes only a P033
`SelectedSchematicBranch@1`. It projects current unresolved discipline
obligations and the exact selected-topology reference into a bounded,
read-only expert snapshot; full history, sibling branches, repositories, MCP
clients, and world handles remain outside that context.

Expert discovery intersects current obligation topics with P039
design-development metadata. Stable discovery order is presentation only: the
Primary Architect selects any valid subset and invocation order. Returned
`DetachedDevelopmentAdvice@1` values contain claims and suggested obligations
but no mutation authority.

The compiler requires the Architect to adopt, reject, or defer every selected
claim. Conflicts cannot become hidden consensus: either one claim is adopted
and the alternatives receive explicit rejection rationales, or the unresolved
set becomes a dependency-bound obligation. Only the Architect's decision may
introduce or revise developed components.

Structure/support, circulation, envelope/openings, materials, construction,
and use remain distinct obligation domains. They are coordination coverage,
not a fixed expert sequence or a library of building answers. Component kinds,
attributes, dimensions, materials, interfaces, and dependencies come from the
current project.

Schematic changes traverse only registered dependency edges. Unrelated changes
cannot invalidate developed work; affected components are removed or reopened,
unaffected components remain, and a later authorized P033 selection explicitly
rebinds preserved components to the new schematic revision. Coordination
completion permits recorded advisory unknowns, but grants no candidate,
hard-usability, MCP, world-write, or canonical-commit authority.

## Quarantined V3 diagnostic pilot

`v3_diagnostic.py` registers one optional expert topic surface for the P011
`v3.gate.load_path_analysis` candidate. Discovery depends only on current
support/load-path obligations and an exact component-graph evidence pointer;
Pack identity and registration order are absent.

The diagnostic translates a project-owned neutral graph whose roles are
`load_source`, `collector`, `support`, and `nonstructural` through the P012
subprocess bridge. V3 returns semantic invariants, not geometry bytes or a
legacy Gold comparison. A detached finding may suggest a new obligation, but
the receipt explicitly has no geometry-edit, hard-gate-waiver, winner,
world-write, or canonical-write authority. Provider failure returns no design
answer and leaves the Primary Architect free to choose another explicit action.

## Precedent adoption (P067)

`precedent.py` gates retrieved knowledge three steps from generation:
quoted facts bind exact character spans inside a named snapshot, a typed
adoption under a named authority promotes them, and each adopted fact
compiles into a build-policy constraint whose provenance chains
constraint to adoption to snapshot to URL. The provider must answer
these constraints through the existing required-response validation.

## Branch-conditioned research

`branch_research.py` separates candidate comparison from research scope. A
project supplies evidence-backed criterion scores, an exact-revision research
profile for every Candidate, and either an automatic or human-in-the-loop
selection rule. Automatic selection is legal only when the
named algorithm authority is allowed by the portfolio policy and both the
minimum score and minimum margin pass; an exact tie always enters HITL even
when the configured minimum margin is zero. Deserialisation and application
replay the winner, threshold, margin, authority, active Candidate heads, and
complete loser set. Otherwise the result is an immutable
`human_review_required` decision and the portfolio is unchanged. A selected
winner becomes `SELECTED`; every unselected candidate is retained as `PARKED`
with the same decision and evidence rather than being deleted.

Selection chooses the winning Candidate's research profile as part of the
same immutable decision; callers cannot select one branch and then freely
substitute another branch's decision universe, vocabulary, or source policy.
The resulting `BranchResearchScope@1` is derived from the exact source
`SchematicOptionSet` and current `OperationalMarkovState`, and retains
content-addressed P036 refs for both source objects; callers cannot inject a
same-run `BranchRef` or later rewrite the profile. It binds research to the
canonical base, source operational branch, candidate portfolio and revision,
full content-addressed P036 selection ref, active decision universe,
branch-defining and excluded search terms, domain allowlist, and context refs.
`BranchPrecedentQuery@1` can narrow but cannot widen that envelope. Branch
research output must echo both the query and scope digests, and an explicit
`PrecedentAdoption@1` must be bound back to the same query before any fact can
enter a branch basis.

`build_branch_basis_index()` ignores legacy and foreign-scope evidence. It
requires an exact query/adoption/`BranchEvidenceSnapshot@1` join by full P036
record URI (never basename), verifies the quote against typed retained text,
and validates both requested and final redirected URLs against the branch
allowlist. It initializes shards from the complete active decision universe
and reports missing decisions instead of silently omitting them.
`compile_next_branch_queries()` opens exactly those missing decisions;
`BranchRAGProgress@1` exposes the covered count, uncovered refs, next-query
refs, and continue/complete status for a later progress panel. A bounded
`BranchDecisionContext@1`, including the full verified scope, is compiled by
the runtime only after the P036 index is reloaded and re-derived. The public
semantic capability cannot claim or inject persisted branch evidence. The
private production authoring path additionally requires the current
operational state and a runtime-derived expected scope digest, so a valid
sibling Candidate context cannot be substituted.
