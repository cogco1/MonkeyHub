# P080 — Stage convergence and inherited-state receipt

- Origin: Planning (state inheritance and bounded convergence)
- Status: Done
- Depends on: P039, P040, P041, P068, M075

## Goal

Make staged design convergence machine-checkable. Compare exact parent and child
operational states through a project-supplied convergence policy, prove protected
locks and dependency-local inheritance, and classify each transition as genuine
progress, explicit scope expansion, repair, or rejection rather than treating any
valid state delta as forward progress.

## Acceptance

- The receipt binds branch, stage, exact parent/child digests, transition kind,
  protected locks, mandatory obligations, hard-gate failures, conflicts,
  tolerance failures, invalidations, and revalidations.
- Refine, resolve, and repair transitions preserve protected locks and strictly
  reduce a deterministic convergence vector; a no-op or regression is rejected.
- Scope expansion is explicit, bounded, authorised, and never reported as
  convergence progress. Reopening a locked decision expands only its declared
  dependency closure.
- Stale parent state, unexplained invalidation, unclosed mandatory obligation, and
  unauthorised lock change fail closed.
- Historical and modern synthetic cases prove stage inheritance without relying
  on axial symmetry or any building-type vocabulary in the framework.

## Stop conditions

- Stop if the evaluator accepts a stale base, hidden lock change, no-op, or wider
  invalidation set than the supplied dependency closure.
- Stop if adding obligations is presented as ordinary progress.

## Tests

Exact-base binding, vector monotonicity, lock inheritance, bounded reopening,
repair, scope expansion, and historical/modern synthetic fixtures; architecture
firewall.


## Completion

- Completed: 2026-08-28
- Evidence: StageConvergencePolicy and exact-state evaluator bind parent/child digests, protected locks, mandatory obligations, external gate/conflict/tolerance/revalidation deficits, lexicographic potential, and dependency-local scope expansion. Refine/resolve/repair require strict decrease; ordinary hidden expansion and protected drift reject; authorised grid revision is typed scope_expansion. Historical colonnade and modern grid/core/facade/MEP synthetic tests: 10 green; compileall and architecture firewall pass.
