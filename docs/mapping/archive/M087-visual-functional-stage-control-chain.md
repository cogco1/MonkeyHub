# M087 — Visual-to-functional stage-control chain

- Origin: Modify
- Status: Done
- Depends on: M083, M084, M085, M086

## Goal

Connect the reusable visual inventory, semantic component inventory, function
contracts, architectural relation authoring, physical realization, stage
artifact, and CAD execution boundaries into one fail-closed production route.
The route must make omissions such as unmarked windows, unsupported pediments,
floating stairs, or unverified load paths visible before stage closure rather
than letting a project runner silently skip them.

## Boundaries

- Visual recognition output remains proposed evidence.  It cannot decide
  component identity, dimensions, topology, design, acceptance, promotion, or
  canonical state.
- Project agents or humans author evidence-bound applicability decisions and
  relation bases.  The framework never invents a project-specific function,
  support path, opening, or geometric answer.
- Runtime compilers return typed values only.  Durable records are written and
  reloaded solely through P036 project ports.
- Stage entry and CAD execution guards grant no persistence, acceptance,
  selection, promotion, or canonical-write authority.
- Historical schemas remain replay-only and cannot satisfy current authoring
  requirements.

## Acceptance

- One production runtime compiles exact image/ROI inventory, accepted physical
  component joins, semantic stage subjects, function ledgers, and mandatory
  relation questions without caller-controlled denominator loss.
- Unknown or unresolved visual components, function applicability, evidence
  bases, relation questions, or relation realization remain typed OPEN/UNKNOWN
  and block the appropriate stage gate.
- Relation traversal and function-rule slots are distinct: support/load paths
  traverse from the supported component to their sink while validating the
  exact function-bearing component.
- Stage 3+ realization and stage artifact claims bind the exact source set,
  coverage denominator, program, readback, retained P036 records, and stage
  entry proof; nonempty record names alone cannot produce VERIFIED status.
- The stage-bound Rhino adapter executes only the guarded plan and returns a
  receipt bound to the exact guard, artifact claim, and raw executor result.
- P036 save/reload mechanically replays exact branch-local visual, function,
  relation, realization, and artifact sources while canonical HEAD remains
  unchanged.

## Tests

- Visual inventory, duplicate/ROI coverage, unknown-component fail closure.
- Component-function applicability, relation-question derivation, relation
  authoring, realization, and stage-control runtime integration.
- Exact Stage 3 artifact construction and stage-bound CAD execution guards.
- P036 durable save/reload and cross-run/branch/epoch tamper rejection.
- Related M083–M086 regressions, compileall, architecture check, and diff audit.

## Stop conditions

- Stop before claiming a live CV model, RAG provider, or human reviewer was
  invoked when the runtime consumed project-supplied typed evidence.
- Stop if a generic compiler guesses a building-specific component, function,
  relation, dimension, support path, or design answer.
- Stop if any new persistent path bypasses P036 or changes canonical HEAD.


## Completion

- Completed: 2026-08-31
- Evidence: 329 related M083-M087 tests passed; P036 StageArtifactArchive replay/tamper/epoch/HEAD integration passed; compileall, ARCHITECTURE PASS 203 files, and diff audit passed.
