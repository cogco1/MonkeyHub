# M034 — Context hook lifecycle-stable test

- Origin: Modify
- Status: Ready after M033
- Depends on: M033, M015

## Goal

Make the M033 acceptance test follow registry-owned card paths and the current
active-card set before and after work-card completion.

## Write scope

- `tests/test_context_recovery_hook.py`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- The guarded M033 card path is read from its registry item after planning to
  archive movement.
- Expected recovery cards are derived from the current registry active set and
  never hard-code M033 or M034 lifecycle state.
- The test still requires every active card to appear in PreCompact and compact
  SessionStart output.
- M033 implementation, hook output, configuration, authority labels, and
  production code remain unchanged.
- Focused tests pass both while M034 is active and after M034 is archived.

## Tests

- Registry-owned completed-card path.
- Current active-card coverage in both hook events.
- Focused context hook suite before and after completion.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the test weakens all-active-card coverage.
- Stop if the repair changes hook behavior or project configuration.
- Stop if a lifecycle state is replaced with another hard-coded card id.


## Completion

- Completed: 2026-08-08
- Evidence: Replaced M033 lifecycle hard-coding with registry-owned card paths and the current active-card set; focused tests, architecture firewall, and full discovery passed before completion.
