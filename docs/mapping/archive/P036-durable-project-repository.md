# P036 — Durable project repository

- Origin: Planning
- Status: Ready after M007
- Depends on: P035, M007

## Goal

Implement the single generic filesystem owner behind the P035 ports: immutable
record/blob ingestion, append-only events, verified canonical snapshots,
atomic `HEAD` compare-and-swap, run recording, reload, and crash recovery.

## Write scope

- `archflow/project/`
- `archflow/workspace/`
- `archflow/state/model.py`
- `archflow/commit/`
- `tests/test_project_repository.py`
- `tests/integration/test_project_restart.py`
- `probes/README.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- All paths are allocated by `ProjectLayout` and remain inside the project.
- JSON records and binary artifacts are immutable, digest-bound, and addressed
  by `project://` references rather than persistent absolute machine paths.
- `project.json` is immutable; event append, canonical snapshot, and `HEAD` CAS
  have a documented crash-recovery order.
- Rejected candidates and speculative work cannot advance `HEAD`.
- Concurrent stale writers fail closed.
- Reload verifies manifest, record digests, event chain, snapshot, and `HEAD`.
- Producers receive injected sinks and cannot choose or create archive paths.

## Tests

- Atomic record/blob ingestion and duplicate-content behavior.
- Path escape, cross-project reference, and absolute-URI rejection.
- Event/snapshot/HEAD crash-point recovery matrix.
- Stale writer and concurrent CAS rejection.
- Full project close/reopen without model, MCP, or raw transcript.

## Stop conditions

- Stop before claiming an atomic transaction with an external Minecraft world.
- Stop if a producer must import the filesystem implementation to choose a path.
- Stop if schema migration would silently reinterpret an older record.

## Implementation evidence

- `FilesystemProjectRepository` is the sole path allocator and filesystem
  writer behind the P035 ports.
- Project manifests, run manifests, JSON records, and binary objects are
  immutable; stable references use `project://` plus SHA-256.
- Accepted exact-base promotion writes snapshot and event before one atomic
  `HEAD` replacement. Rejected and stale writers fail closed.
- Reopen verifies manifest, current snapshot, event chain, and digests.
  Pre-`HEAD` crash artifacts remain non-canonical and are reported as orphans.
- Seven focused repository/restart tests cover ingestion, containment,
  rejected promotion, stale CAS, orphan recovery, and full close/reopen.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented the sole durable project repository with immutable digest-bound records/artifacts, project URIs, exact-base runs, accepted-event preparation, atomic HEAD CAS, verified reload, stale/rejected fail-closed, and orphan crash reporting; 97 full tests, compileall, and scope pass.
