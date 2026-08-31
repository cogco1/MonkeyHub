# M078 — Exact stage entry and CAD externalization hardening

- Origin: Modify
- Status: Done
- Depends on: P036, P071, P072, M069

## Goal

Close the reusable control gaps exposed by cross-building reconstruction:
initialize Stage 0 from exact project evidence, validate project-supplied
architectural invariants without embedding a typology, and externalize one
exact compiled geometry program to Rhino only when complete independent
readback proves identity, semantics, units, bounds, and material projection.

## Acceptance

- Stage 0 rejects historical or ambiguous run/branch/epoch evidence and never
  changes canonical HEAD.
- A developed-design state cannot claim `COORDINATED` while omitting any leaf
  component from the selected schematic semantic tree.
- Architectural counts, partitions, oriented frames, rotational orbits, and
  level strata are claim-bound inputs; missing observations remain UNKNOWN.
- CAD execution binds an exact P036 program record and rejects path escape,
  symlinks, script mutation, stale output, translation loss, or identity drift.
- A successful CAD receipt requires full independent object, semantics,
  material, unit, block, and bounds reconciliation; process exit alone is not
  success.

## Stop conditions

- Stop before weakening exact identity or accepting a partial CAD readback.
- Stop before assigning design, stage-acceptance, promotion, persistence, or
  canonical-write authority to validators or adapters.
- Stop if a project-specific building constant enters the reusable layer.

## Tests

- Stage 0 evidence scope, idempotence, ambiguity, and canonical-HEAD tests.
- Architectural invariant PASS/FAIL/UNKNOWN and stage-closure tests.
- CAD plan containment, stability, execution failure, semantics, materials,
  blocks, and bounds readback tests.
- Architecture check and compileall.


## Completion

- Completed: 2026-08-30
- Evidence: Target suites: 99 passed; repository verification receipt PASS.
- Evidence: Real Rhino COM smoke: succeeded with independent readback, cleanup confirmed, and no residual Rhino process.
- Evidence: Architecture check and compileall passed; canonical-write authority remains false.
