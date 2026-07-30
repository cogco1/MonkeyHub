# M005 — Probe-rooted integration smoke

- Origin: Modify
- Status: Done
- Depends on: M002, P001

## Goal

Make a building integration test behave like a real case: a data-only probe
provides input, one generic smoke runner calls public framework modules, and
every generated state, receipt, workspace artifact, and run record is written
under that probe rather than into framework or test source directories.

## Write scope

- `tools/probe_smoke.py`
- `probes/`
- `tests/test_probe_hierarchy.py`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- `probes/test_library/` contains project input and framework-produced run
  records but no executable case logic.
- The input contains no preselected dimensions, spaces, materials, topology,
  coordinates, geometry commands, or expert order.
- The generic runner contains no building-type branch or project answer.
- One explicit smoke touches the current public state, workspace, runtime,
  adapter, submission, validation, evaluation, and commit boundaries.
- Every generated path resolves inside `probes/test_library/runs/<run_id>/`.
- The record declares `synthetic=true`, `generation_authority=false`, and
  cannot be interpreted as architectural usability proof.
- Unit tests remain framework-contract tests; project integration evidence
  remains probe-owned.

## Tests

- Probe input/schema and no-executable scan.
- Generic-runner instance-literal scan.
- Persisted run-record and artifact containment.
- Full test suite, compileall, registry, and Markdown links.

## Stop conditions

- Stop if the runner must derive a building answer before P020-P024.
- Stop if an artifact, state, or receipt would be written into `archflow/` or
  `tests/`.


## Completion

- Completed: 2026-07-25
- Evidence: Created data-only probes/test_library input with no dimensions, spaces, materials, topology, geometry commands, or expert order; the case contains no executable logic.
- Evidence: Generic tools/probe_smoke.py exercised state, workspace, runtime, adapter, submission, validation, evaluation, and commit boundaries and wrote candidate, validation, evaluations, commit, canonical state, workspace artifact, digests, and manifest only under probes/test_library/runs/framework-smoke-002/.
- Evidence: The run declares synthetic=true, generation_authority=false, framework_boundary_smoke=true, and architectural_usability_proven=false; 77 tests, compileall, scope, registry, Markdown links, runner-literal scan, and output-containment scan pass.
