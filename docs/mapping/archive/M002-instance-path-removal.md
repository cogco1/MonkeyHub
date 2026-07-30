# M002 — Remove the instance-owned generation path

- Origin: Modify
- Status: Done
- Depends on: P019

## Goal

Delete the retired executable case, its fixed design answers, run archives,
compatibility path, and case-owned tests. Rebase future external validation on
one generic runner that loads data-only building probes and calls the P020-P024
framework pipeline.

## Framework/case boundary

- `archflow/` owns schemas, compilers, state reducers, capability discovery,
  validators, archive formats, execution boundaries, and the generic runner.
- `probes/<project_id>/input/` owns the raw request, authorized evidence,
  explicit user constraints, site observations, and run policy for one
  building.
- `probes/<project_id>/runs/` owns only framework-produced, versioned outputs:
  derived facts, commitments, program hypotheses, design states, alternatives,
  decisions, receipts, candidates, and artifacts.
- A probe cannot contain Python generation logic, a custom Architect, a fixed
  expert schedule, derived spatial answers masquerading as inputs, a geometry
  script, or its own archive/checkpoint implementation.

## Write scope

- `probes/`
- `.runs/live-library/`
- `tests/`
- `archflow/runtime/README.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- The retired executable case, its run evidence, and its compatibility junction
  no longer exist.
- No test, runtime module, registry item, roadmap lane, or active document
  imports or depends on that case path.
- Generic transition, environment-feedback, adapter, and repair mechanisms
  retain neutral framework-level tests.
- Probe inputs and framework-produced outputs are structurally distinct.
- P020 owns the future data-only probe envelope and generic loader; P026 follows
  P020-P024 and cannot recover a case-owned generator.

## Tests

- Full unit and integration suite.
- Framework/probe reverse-import and instance-literal scan.
- Registry render and Markdown-link validation.
- Compileall.

## Stop conditions

- Stop if deletion would remove a reusable production contract from
  `archflow/`; preserve that contract and move only neutral tests.
- Do not fabricate a replacement design compiler before P020-P024.


## Completion

- Completed: 2026-07-25
- Evidence: Retired executable case, fixed project answers, historical runs, compatibility junction, and case-owned tests removed; generic transition, environment-feedback, adapter, and repair contracts retained under neutral framework tests.
- Evidence: Data-only probe boundary documented: inputs and framework-produced runs are separate; probes cannot own Architect logic, expert schedules, spatial defaults, geometry scripts, validators, reducers, or loaders.
- Evidence: 67 tests, compileall, scope check, registry render, reverse-import scan, instance-literal scan, and Markdown link validation pass.
