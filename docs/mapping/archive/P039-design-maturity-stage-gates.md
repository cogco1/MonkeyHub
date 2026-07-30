# P039 — Design maturity graph and phase-gated experts

- Origin: Planning
- Status: Ready after M009
- Depends on: P006, P019, M009

## Goal

Define a deterministic design-maturity graph so expert capabilities remain
dynamic inside the current phase but cannot produce or validate deliverables
from a later phase. The graph models architectural precedence without turning
expert registration order into a fixed schedule.

```text
research / brief
  -> programming
  -> site and resource coordination
  -> schematic design
  -> design development
  -> candidate coordination
  -> execution ready
```

Revision may move backward and invalidate downstream deliverables. Forward
movement may not skip a phase or rely on an expert's assertion that a phase is
complete.

## Write scope

- `archflow/state/design_maturity.py`
- `archflow/capabilities/phase_gates.py`
- `archflow/state/__init__.py`
- `archflow/capabilities/README.md`
- `tests/test_design_maturity.py`
- `docs/ARCHITECTURE.md`
- `docs/mapping/`

## Acceptance

- Each `D_v,k` names one design phase and exact-base phase-deliverable refs.
- Forward transitions require the prior phase's typed deliverable roles and
  deterministic gate receipt.
- Skipped, stale, cross-branch, and expert-self-certified phase transitions
  fail closed.
- Backward revision is allowed only with explicit invalidation of affected
  downstream deliverables and new obligations.
- Backward closure traverses only M009 invalidation/revalidation dependency
  effects; support-only and obligation-blocking edges cannot falsely stale a
  deliverable or obligation.
- Expert specs declare allowed phases. Discovery is the intersection of
  current phase, current obligations, evidence, and capability metadata.
- Expert order remains flexible within a phase; registration order is never
  execution order or design authority.
- The graph contains deliverable roles and maturity rules, not building
  dimensions, topology, materials, or a mandatory named-expert sequence.

## Tests

- No-skip forward phase matrix.
- Backward revision and downstream invalidation.
- Wrong-phase expert exclusion.
- Two valid non-isomorphic expert orders inside one phase.
- Stale and cross-branch phase receipt rejection.
- No building-answer or fixed-expert-order scan.

## Stop conditions

- Stop if phase completion is inferred from model confidence.
- Stop if every project is required to call the same experts.
- Stop if backward revision leaves downstream work falsely current.


## Completion

- Completed: 2026-07-26
- Evidence: Implemented DesignMaturityState@1 with seven generic phases and exact branch/state-bound typed deliverables; deterministic no-skip PhaseGateReceipt rejects stale, cross-branch, missing-role, and expert-certified transitions; backward revision traverses only INVALIDATES/REQUIRES_REVALIDATION and emits local open obligations; phase-aware expert discovery intersects obligations, evidence, phase, and metadata while preserving Architect-selected order. Verified 10 P039 tests, full 184-test suite (1 opt-in skip), compileall, purity scan, and scope PASS.
