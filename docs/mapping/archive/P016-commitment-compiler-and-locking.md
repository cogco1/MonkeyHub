# P016 — Commitment compiler, ambiguity, and locking

- Origin: Planning
- Status: Ready after M007
- Depends on: P015, M007

## Goal

Compile bounded brief facts and authorized events into proposed commitments,
while keeping ambiguous user intent explicitly uncertain until an authorized
confirmation activates it.

## Write scope

- `archflow/runtime/commitment_compiler.py`
- `archflow/runtime/README.md`
- `archflow/state/`
- `tests/test_commitment_compiler.py`
- `docs/mapping/`

## Boundary

```text
brief/event observation
  -> candidate interpretations with evidence
  -> proposed commitment
  -> confirm / revise / reject
  -> accepted or active commitment in canonical state
```

The compiler may expose alternatives and confidence, but probability cannot
substitute for authorization.

## Acceptance

- Explicit size/use requirements compile deterministically into proposed typed
  commitments with source evidence.
- Ambiguous language produces named alternative interpretations rather than a
  guessed active constraint.
- Activation requires an authority permitted by the commitment's revision
  policy.
- Confirmed commitment values cannot be replaced by later prompt text without
  an explicit revision event.
- Missing evidence stays unknown and cannot be filled by Pack defaults.
- Compiler output is detached and cannot mutate canonical state.

## Tests

- Explicit one-line requirement compilation.
- Ambiguous target/minimum/maximum alternatives.
- Confirmation and parameter locking.
- Unauthorized replacement rejection.
- Missing-evidence and no-Pack-default behavior.

## Stop conditions

- Stop if confidence alone activates a commitment.
- Stop if later text overwrites an accepted commitment without a revision.
- Stop if building-specific defaults enter the generic compiler.

## Implementation evidence

- Added typed exact/minimum/maximum/target intent terms and evidence-bound
  observations.
- Explicit interpretations compile deterministically to proposed commitments;
  ambiguous meanings remain multiple proposals and missing evidence remains
  unknown.
- Named authority confirmation is required before a hard commitment becomes
  active.
- Later text cannot replace a lock. Explicit authorized revision preserves
  predecessor/successor lineage.
- The compiler is frozen, detached, building-agnostic, and has no filesystem,
  Pack, canonical-state, or project-output writer.
- Six focused tests cover explicit, ambiguous, authority, locking, revision,
  missing-evidence, and instance-default boundaries.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented deterministic evidence-bound intent terms, ambiguity-preserving proposals, named-authority confirmation, hard locks, explicit revision lineage, and missing-evidence unknowns with no building/Pack defaults or writer; 106 full tests, compileall, scope, and map pass.
