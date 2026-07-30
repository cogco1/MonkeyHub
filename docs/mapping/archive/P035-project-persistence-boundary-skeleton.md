# P035 — Project persistence boundary skeleton

- Origin: Planning
- Status: Ready after M006 and P019
- Depends on: M006, P019

## Goal

Establish the generic, side-effect-free project document boundary before any
new compiler, expert, model, or controller decides where its records belong.
This card defines project identity, paths, logical references, and persistence
ports; it does not implement durable writes.

## Write scope

- `AGENTS.md`
- `archflow/project/`
- `tests/test_project_boundary.py`
- `tests/test_devctl.py`
- `tools/devctl.py`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `probes/README.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- `project.json` contains immutable project identity/schema only; mutable
  current-version state belongs to a separate `HEAD`.
- Project, run, branch, record, and artifact identities are distinct contracts.
- Project-relative references resolve only inside the owning project.
- The layout names input, object, event, canonical, run, candidate, review,
  workspace, recovery, and export ownership without creating directories.
- Persistence ports have no filesystem implementation in this card.
- An unassigned persistence destination fails closed with an instruction to ask
  the project owner.
- Project-local agent rules forbid improvised writes into framework, docs,
  tests, or repository-level run directories.
- The generated dynamic map recognizes the project module and persistence
  architecture layer.

## Tests

- Manifest and reference round trips.
- Project path containment and traversal rejection.
- Layout construction has no filesystem side effect.
- Unassigned destination fails closed.
- Full tests, compileall, registry, map, and Markdown links.

## Stop conditions

- Stop before migrating the existing `StateRef`, `GoalContract`, or committer.
- Stop before implementing file writes, locks, CAS, event append, or artifact
  ingestion; those require the M007 identity repair and P036 durable store.


## Completion

- Completed: 2026-07-25
- Evidence: Added side-effect-free archflow/project manifest, layout, typed project/run/branch/record/artifact refs, project URI, persistence ports, and fail-closed destination guard; added project-local stop-and-ask rule; planned M007 identity repair and P036 durable repository; 86 tests, compileall, scope, map, and 54 Markdown files passed.
