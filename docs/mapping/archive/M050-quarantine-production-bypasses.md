# M050 — Quarantine production bypasses

- Origin: Modify
- Status: Ready
- Depends on: P056, P031, P053

## Goal

Remove old routes from the production chain after the formal runtime owns their
responsibilities. Keep test doubles and platform adapters only behind explicit
compatibility or downstream boundaries, with no fallback into production.

## Write scope

- `archflow/runtime/`
- `archflow/adapters/`
- `archflow/production/`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- `python -m archflow.runtime` delegates to the formal runtime or fails with a
  named migration message; it cannot run `FakeArchitect` or `FakeVoxelAdapter`
  as production.
- Fake providers remain importable only as test fixtures and cannot register
  or receive active production authority.
- Minecraft MCP and other platform adapters consume accepted neutral artifact
  or typed export requests; arbitrary JSON plans cannot enter candidate
  assembly as design authority.
- Primary Architect and geometry authoring no longer use direct provider
  injection on the active path; P053 owns binding, shadow, and cutover.
- No retired route is used as an implicit fallback when the active provider
  fails.

## Tests

- Static and runtime absence of FakeVoxel production entry.
- Arbitrary MCP payload and direct-provider bypass rejection.
- Exact-base provider cutover, no-fallback behavior, compatibility diagnostics,
  architecture firewall, and full unit discovery.

## Stop conditions

- Stop before deleting a route until P056 proves its named replacement.
- Stop if downstream export gains design, validation, or canonical-write
  authority.


## Completion

- Completed: 2026-08-16
- Evidence: Formal runtime module delegates to the P056 run-project CLI; Primary Architect and ProductionRootCompiler reject direct providers unless P053-authorized; marked test-only providers cannot activate; active provider failure has no fallback; typed MinecraftExportRequest binds downstream translation to a project-owned neutral package and acceptance record with zero design, validation, persistence, or canonical-write authority, while raw plan methods remain compatibility-only. M050 focused and recovery verification passed 78 tests; full discovery passed 523 tests with 3 explicit opt-in skips; ARCHITECTURE PASS 116 files; V3 boundary PASS 116 production files and 2 frozen oracles; explicit work-card scope PASS 24 paths.
