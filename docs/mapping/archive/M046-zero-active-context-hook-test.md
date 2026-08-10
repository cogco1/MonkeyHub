# M046 — Zero-active-card compaction test

- Origin: Modify
- Status: Done
- Depends on: M034

## Goal

Make the compaction recovery acceptance test valid when the registry has no
active work card, without inventing write scope or changing hook behavior.

## Write scope

- `tests/test_context_recovery_hook.py`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A zero-active-card recovery names that state and does not fabricate a card.
- Write scope, stop conditions, and capsule claims remain mandatory whenever
  at least one active card exists.
- Coverage continues to derive the expected card set from the registry.
- The production hook and project configuration remain unchanged.

## Tests

- Focused context recovery hook suite with no active card.
- Architecture firewall.
- Full unittest discovery.

## Stop conditions

- Stop if the repair changes production hook output.
- Stop if the repair hard-codes a work-card identity.
- Stop if active-card scope and stop-condition coverage is weakened.


## Completion

- Completed: 2026-08-10
- Evidence: tests/test_context_recovery_hook.py now derives active-card assertions conditionally and directly exercises a synthetic zero-active-card recovery without changing the production hook.
- Evidence: Focused context recovery tests passed 5 cases; architecture firewall passed; full discovery passed 462 tests with 3 skips.
