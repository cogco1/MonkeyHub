# M082 — Exact architectural relation realization closure

- Origin: Modify
- Status: Done
- Depends on: P077, M079

## Goal

Require every declared architectural relation to bind its semantic endpoints
to exact compiled objects and exact CAD readback objects, then bind explicit
pairings or paths to independent checks before that relation can satisfy a
stage requirement.

## Acceptance

- Relation realization is bound to one exact branch, scope, relation graph,
  compiled program, readback snapshot, and stage-subject digest.
- Every semantic endpoint names its exact semantic binding, compiled output,
  producing operation, and readback object; missing or ambiguous bindings fail
  closed.
- Multi-object endpoints use caller-authored pairings and ordered paths. No
  Cartesian expansion or `GeometryOperation.input_object_ids` inference is
  allowed.
- Walking, load, support, host-interface, and contact-interface purposes remain
  independent. A walking-path pass cannot satisfy a support chain.
- Object bindings without an exact independent narrow-phase receipt remain
  `UNKNOWN`, and a requirement-first bridge can place that outcome on the
  composite stage-closure path without granting acceptance or write authority.
- No building name, dimension, stair count, roof type, or project answer enters
  the reusable contracts or checker.

## Tests

- Relation-realization contract round-trip and exact identity joins.
- Missing endpoint, duplicate readback, cross-branch, explicit pairing/path,
  narrow-phase UNKNOWN, purpose isolation, and composite-stage closure tests.
- Architectural relation, coverage, authoring, verification, assembly,
  walking-surface, and CAD-readback regressions.
- Architecture check, compileall, and diff check.

## Stop conditions

- Stop before interpreting geometry-operation consumption edges as
  architectural support or interface relations.
- Stop before automatically accepting a relation from proximity or AABB
  overlap.
- Stop before making one path purpose stand in for another discipline.
- Stop before giving a checker design, stage-acceptance, commit, persistence,
  or canonical-write authority.


## Completion

- Completed: 2026-08-30
- Evidence: Exact branch/scope/graph/program/readback/stage-subject binding; explicit pairings and ordered purpose-isolated paths; fail-closed endpoint joins and UNKNOWN without independent narrow phase; 124 related tests, architecture check, and compileall passed.
