# Architecture decision records

One file per decision a later session would be tempted to reverse. Each opens with the decision, why
and do-not; the number in `NNN-short-name.md` is the id other documents cite, as in ADR-006. Below its
decision, ADR-001 keeps the 2026-09-03 consolidation record it came from.

- [ADR-001](001-one-spine.md) — One production spine
- [ADR-002](002-rhino-is-not-the-source-of-truth.md) — Rhino is an executor, not the source of truth
- [ADR-003](003-two-identities.md) — Two identities, never three
- [ADR-004](004-authority-flags-stay-serialised.md) — The historical authority block stays where retained digests bind it
- [ADR-005](005-content-addressed-records-do-not-name-themselves.md) — A content-addressed record cannot carry its own reference
- [ADR-006](006-semantics-compile-to-registered-ids.md) — Architectural semantics compile to registered ids
- [ADR-007](007-container-states-and-stage-rules.md) — Four container states; a stage belongs to the run
- [ADR-008](008-one-tree-many-projections.md) — One decision tree, many projections: a derived index and a content-keyed cache
- [ADR-009](009-memory-layer.md) — Memory is its own layer: `studio.memory`, beside State, not inside decisions
- [ADR-010](010-admission-is-the-agents-registration.md) — An admission is the Agent's registration of a finished loop, not the architect's endorsement
- [ADR-011](011-interface-information-hierarchy.md) — One information hierarchy on every surface: text menus, focused instruments, status text
- [ADR-012](012-the-open-runtime-is-the-only-writer.md) — The open project's runtime is its only writer; changes from elsewhere are found at open and on refresh
- [ADR-013](013-actor-working-heads.md) — Authenticated actors own separate working heads and recovery
