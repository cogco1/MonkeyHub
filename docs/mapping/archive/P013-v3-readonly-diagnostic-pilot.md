# P013 — First V3 read-only diagnostic pilot

- Origin: Planning
- Status: Done
- Depends on: P006, P012

## Goal

Migrate one building-neutral V3 structural or topology diagnostic through the
P012 bridge and prove that a useful V3 inheritance can participate as a
read-only, state-responsive capability rather than regain design ownership.

## Write scope

- `archflow/capabilities/v3_diagnostic.py`
- `archflow/capabilities/README.md`
- `tests/integration/test_v3_diagnostic_pilot.py`
- `tests/fixtures/v3_legacy/`
- `probes/p013-diagnostic-control/`
- `probes/p013-diagnostic-contrast/`
- `docs/mapping/`

## Acceptance

- One neutral input contract works for at least two building cases, including
  one case outside the capability's origin typology.
- The capability is discovered from current obligation topics and evidence,
  not from Pack identity or registration order.
- Output is a detached observation or expert receipt bound to the exact base
  state and input artifact digest.
- Comparison with V3 uses named semantic invariants, not byte-identical
  geometry or legacy Gold equality.
- Diagnostic findings can create obligations but cannot edit geometry, waive a
  hard gate, select a winner, or commit state.
- Provider failure is bounded and fail-open with respect to design authorship.
- A non-migratable Pack dependency is reported by name rather than copied into
  V4.
- Both retained case inputs and framework-produced evidence stay within their
  respective data-only probe projects.

## Tests

- Two-building semantic-invariant probe.
- State-responsive discovery and registration-order independence.
- Exact-base, digest, and detached-input checks.
- Offline/error receipt and canonical-state preservation.

## Stop conditions

- Stop if the diagnostic requires typology defaults to interpret neutral input.
- Stop if a second building cannot use the same contract.
- Stop if semantic comparison can only pass by reproducing V3 bytes.

## Implemented boundary

- The shared input is `NeutralComponentGraph@1` with generic load roles and no
  Pack, typology, material, or building-name field.
- Discovery intersects current load-path/support obligations with an exact
  component-graph evidence pointer; registration order remains presentation
  only.
- A dedicated external process imports only the V3 load-trace slice and fails
  if Pack, examples, apps, LLM support, or decision modules load transitively.
- The control frame and non-origin transit-canopy probes compare grounded-load,
  finding, unsupported-component, and support-path invariants. No geometry or
  legacy Gold bytes are compared.
- Findings remain suggested obligations in a detached receipt. Provider
  failure returns no suggestion and cannot edit geometry, waive validation,
  choose a winner, write a world, or advance canonical state.


## Completion

- Completed: 2026-07-28
- Evidence: Proved one dedicated V3 load-trace slice runs in an external read-only process without loading apps, examples, Packs, LLM support, decision.v2_stages, or composer surfaces.
- Evidence: The same NeutralComponentGraph@1 contract produced named grounded-load, finding, unsupported-component, and support-path invariants for a grounded control frame and a non-origin transit-canopy contrast; exact inputs and receipts remain in two generic project repositories under probes.
- Evidence: Discovery follows current load-path obligations plus component-graph evidence; output can only suggest obligations and has no geometry, hard-gate, aesthetic, world, persistence, or canonical authority. Five focused tests and 303 full tests passed with 1 external smoke skipped; architecture firewall passed 89 files, compileall and seven-path scope check passed.
