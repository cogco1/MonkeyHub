# P055 — Joint semantic-geometry lifecycle

- Origin: Planning
- Status: Ready
- Depends on: P054, P051, P036

## Goal

Compile one exact-base lifecycle transaction in which component creation,
revision, or retirement and its detailed neutral geometry changes either
succeed together or fail together. Make dependency invalidation and unaffected
sibling preservation explicit before any project persistence.

## Write scope

- `archflow/state/`
- `archflow/runtime/`
- `archflow/realization/`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- One typed transaction binds predecessor component and geometry digests and
  returns successor records plus a joint lifecycle receipt.
- Added, revised, and retired component ids have matching geometry creation,
  revision, rebind, or retirement evidence; partial application is impossible.
- Dependency invalidation is explicit and bounded; unrelated sibling component
  and geometry digests remain unchanged.
- Coarse volumes may deepen into shell, opening, assembly, or authored asset
  geometry without losing the stable parent component identity.
- The transaction returns values only. P036 performs the later compare-and-swap
  persistence and no in-memory committer becomes a second canonical writer.

## Tests

- Dome massing to shell-and-oculus to shell-local coffering lifecycle.
- Sibling portico preservation and explicit cross-component invalidation.
- Stale component base, stale geometry base, undeclared retirement, orphaned
  binding, half-transition, and duplicate ownership rejection.
- Architecture firewall and full unit discovery.

## Stop conditions

- Stop if semantic and geometry state can commit independently.
- Stop if transaction code chooses a project path or mutates P036 state.


## Completion

- Completed: 2026-08-16
- Evidence: Implemented a pure exact-base SemanticGeometryLifecycle transaction that composes the existing component transition and geometry compiler, exposes both successors only on joint success, and carries no path, persistence, or canonical-write authority.
- Evidence: Invalidated components now require changed geometry or explicit revalidation; exact geometry retirement and preserved direct-object digests are enforced. Five focused lifecycle tests cover three-stage dome refinement, sibling preservation, half-transition rejection, exact-base mismatch, and paired retirement; 484 full tests with 3 explicit skips, compileall, architecture firewall, and V3 boundary passed.
