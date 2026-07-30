# P022 — Nested, phase-aware operational design controller

- Origin: Planning
- Status: Done
- Depends on: P006, P008, P017, P018, P021, P028, P039, P043

## Goal

Integrate a global-concept-governed tree of branch-local `D_v,k` states,
state-responsive expert discovery, the primary Architect, proposal/delta
separation, verified transitions, and bounded stop conditions into one
resumable asynchronous design controller.

The current design-maturity phase constrains admissible deliverables and expert
capabilities. Current obligations select dynamically inside that phase. The
framework must not encode a fixed expert order or a building-specific answer.

## Write scope

- `archflow/state/design_state.py`
- `archflow/runtime/design_controller.py`
- `archflow/runtime/operational_transition.py`
- `archflow/runtime/README.md`
- `archflow/state/__init__.py`
- `archflow/runtime/__init__.py`
- `tests/test_design_controller.py`
- `tests/test_operational_transition.py`
- `tests/integration/test_design_state_resume.py`
- `governance/architecture_policy.json`
- `governance/work_registry.json`
- `docs/mapping/`

## Nested state boundary

```text
global concept
  -> phase
    -> discipline/system
      -> component
        -> parameter/detail
```

Each node owns one future-relevant operational state. Lower nodes are more
specific and cannot widen their parent's mutation authority. An Agent receives
only its root-to-node projection: global concept facts, inherited commitments,
relevant ancestor facts, current obligations, explicit cross-node interfaces,
permissions, and evidence. Unrelated sibling states and the append-only event
history remain outside the context slice.

## Acceptance

- `C_v`, nested `D_v,k`, and `H<=t` have distinct schemas, authority, and
  digests.
- The state tree has exactly one global concept root and typed phase,
  discipline, component, and detail paths.
- Child mutation authority can only remain equal or narrow; a local Agent
  cannot mutate an ancestor or unrelated sibling.
- Cross-professional relationships are typed `InterfaceConstraint` objects,
  not hidden prose or copied sibling state.
- `ContextSliceCompiler` preserves global commitments and relevant interfaces
  while proving unrelated sibling state is omitted.
- Local deterministic closure reopens only interface targets named by changed
  references; support-only or unrelated branches stay closed.
- Each operational node carries one P039 phase and exact phase-deliverable
  references.
- Expert discovery is recomputed from current obligations inside the current
  phase. It is not a flat cross-phase pool or fixed within-phase schedule.
- Architect actions must cite non-empty `responds_to_refs` that resolve to a
  current obligation, commitment, interface, clarification, or phase task.
- Forward phase transitions require a deterministic P039 gate. Backward
  revision invalidates affected downstream work and creates obligations.
- Conflicting expert advice remains detached; the Architect cites adopted and
  rejected advice and records the trade-off rationale.
- Evidence conflicts remain typed obligations. Model confidence alone cannot
  resolve them.
- Missing user authority produces a P043 pause/request and resumes only from an
  exact-base authority receipt.
- A mid-run user requirement enters as a P018 evidence-bound event, never as
  direct state mutation. Compatible additions create proposed commitments;
  locked conflicts require authorized revision and local dependency impact.
- Proposal is not fact; only deterministic compile or verified observation
  produces the next node state.
- Stale base, repeated plan, no progress, unresolved authority, and budget exits
  are explicit and reloadable.
- Resume and history use P018 event/state reconstruction; the controller cannot
  invent a parallel checkpoint or repository-level run store.

## Tests

- State tree/context slice serialize and reload without raw history.
- Global concept commitments reach a detail Agent while unrelated sibling facts
  do not.
- A local change reopens only named cross-node interface targets.
- Child authority widening and cross-discipline mutation fail closed.
- Two obligation orders produce valid non-fixed expert sequences in one phase.
- Wrong-phase expert output and skipped-phase proposals fail closed.
- Backward revision invalidates downstream deliverables before resume.
- Conflicting experts produce one explicit decision receipt without automatic
  consensus or hidden ranking.
- Human-clarification pause and exact-base resume.
- Mid-run compatible addition, constraint tightening, locked conflict, and
  dependency-local phase regression.
- Resume from saved state without raw transcript.
- No canonical write before accepted candidate commit.

## Current evidence

- `DesignStateTree@1`, `StatePath`, and `DesignStateNode` implement the complete
  five-level hierarchy with one global root, narrowing authority, one current
  P039 phase, and exact local phase-deliverable references.
- `InterfaceConstraint` provides typed cross-node invalidation,
  revalidation, or support-only relationships.
- `ContextSliceCompiler` compiles root/ancestor/local facts, inherited current
  commitments, active obligations, current phase deliverables, relevant
  interfaces, permissions, evidence, and an explicit omitted-node set.
- `compile_nested_decision` reuses P041 exact-base deterministic closure and
  reports only named downstream nodes that must reopen. Commitment and
  obligation scope refs participate in the same explicit interface closure.
- `design_controller.py` now compiles one bounded turn, recomputes phase-local
  experts from current obligations, preserves Architect-selected order, and
  requires every advice receipt to be adopted or rejected with rationale.
- Exact-base P043 pause/resume, authority-receipt grounding, repeated-plan,
  stale-base, no-effect, and iteration-budget stops are typed and
  serialization-safe.
- P039 forward gates require all active obligations to close. Backward
  revisions rephase the tree, invalidate only typed deliverable dependency
  closure, and attach local repair obligations.
- Mid-run requirements require a verified P018 event chain. Compatible input
  creates only proposed commitments; a locked conflict preserves the lock,
  creates an authorized-revision obligation, and propagates through cited
  interfaces only.
- Focused controller, compatibility-transition, P039, P041, P043, P018,
  repository, and restart regressions pass.
- M014 now proves one semantic state digest across P018 and format-version-2
  P036 `HEAD`/`RunRef`, while preserving snapshot-record integrity separately.
- `ProjectControllerArchiveAdapter` now implements P018 `EventRecordStore`
  against the existing P036 run/branch record area and persists derived
  checkpoints through the same repository.
- Durable reload verifies project/run/branch identity, semantic exact base,
  checkpoint digest, full P018 event prefix, and event-head digest. Restart
  tests prove that no standalone checkpoint directory, mutable latest pointer,
  or canonical `HEAD` advance is introduced.
- The legacy operational trace path writer was removed. It now compiles a pure
  repository-ready payload and validates a repository-loaded payload by
  deterministic replay, so P022 owns no direct filesystem persistence.

## Stop conditions

- Stop if the controller chooses architectural answers for the Architect.
- Stop if branch-local progress can mutate `C_v`.
- Stop if sibling state is copied merely to make an Agent context complete.
- Stop if checkpoints create a second history or persistence authority.


## Completion

- Completed: 2026-07-26
- Evidence: ProjectControllerArchiveAdapter stores P018 events and derived checkpoints through one P036 run/branch repository area with semantic exact-base, event-prefix, digest, restart, no-HEAD-drift, and cross-branch rejection proofs; legacy trace persistence is now pure payload compilation/loading; 228 tests passed with 1 external smoke skipped, architecture firewall and compileall passed.
