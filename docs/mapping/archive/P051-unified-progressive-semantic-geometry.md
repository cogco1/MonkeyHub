# P051 — Unified progressive semantic geometry

- Origin: Planning
- Status: Done
- Depends on: P022, P023, P024, P040, P047, P050, P053

## Goal

Make one stable building-component identity carry semantic meaning and its
current geometry from coarse massing through later detail. Remove the
`CandidateProgramProjection` transport and the P026-only
`semantic_components` production bypass so downstream compilation consumes
the real selected schematic, coordinated design state, commitments, and
component tree directly.

P022 remains the control, authority, and context-slicing tree. It does not
become a second building-composition authority.

## Write scope

- `archflow/state/`
- `archflow/capabilities/`
- `archflow/runtime/`
- `archflow/realization/`
- `archflow/validation/`
- `tests/`
- `probes/p026-sandbox-gold/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- One component tree owns stable component ids, parent-child composition,
  semantic intent, maturity, unresolved child roles, evidence, and the coarse
  massing-volume relation.
- P022 keeps only control/context authority; P040 developments reference the
  same component ids instead of defining another component kind identity.
- Geometry semantic bindings name component ids directly and every generated
  object has one owning component; cross-component effects use explicit
  dependency references rather than shared ownership.
- A component may begin as a semantic coarse volume and later add child
  components and geometry without changing its stable identity.
- Exact-predecessor compilation requires revision or retirement receipts for
  affected geometry and preserves unrelated sibling component digests.
- `CandidateProgramProjection`, its validator, candidate-value ids, and the
  P026-only `semantic_components` production path are removed.
- Candidate assembly, geometry proposal production, validation, player
  inspection, and sandbox realization consume real design records directly.
- Historical P026 artifacts remain data-only evidence and cannot become a
  production fallback or a second schema authority.
- P036 remains the only project persistence authority and P053 remains the
  only production-provider handover authority.

## Tests

- Coarse dome node with geometry and unresolved children.
- Same-id expansion to shell and oculus, then shell-local coffering detail.
- Sibling portico preservation under dome-local revision.
- Missing parent, component cycle, multiple object owners, stale component
  revision, and undeclared retirement rejection.
- Candidate and geometry compilation from real selected/developed state with
  no projection object.
- Static absence of production `CandidateProgramProjection`, candidate-value
  bindings, and P026-only semantic-component authoring.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if implementation creates a second semantic tree or persistence path.
- Stop if legacy records can silently enter new production through fallback.
- Stop if a geometry object can have ambiguous component ownership.
- Stop before calling Pantheon longitudinally proven; P038 owns that evidence.


## Completion

- Completed: 2026-08-10
- Evidence: Unified selected schematic DesignComponent identities with coarse volume ownership and progressive same-id transitions; candidate assembly and geometry proposal/compiler now consume DevelopedDesignState directly; deleted CandidateProgramProjection, its validator, and the P026 sandbox_gold semantic-components runtime bypass; old P026 records fail closed as historical data only; 436 tests passed with 2 skips, compileall passed, architecture firewall passed 100 files, P051 scope passed 34 paths, git diff check passed, and the P051 verification receipt passed all 3 commands.
