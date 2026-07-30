# M015 — Context card lifecycle test

- Origin: Modify
- Status: Done
- Depends on: P046

## Goal

Repair the P046 acceptance test exposed after completion: the context capsule
correctly follows the registry card into Archive, but the test still expects a
hard-coded Planning path.

## Write scope

- `tests/test_devctl.py`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- The expected current-card source path is read from the P046 registry item.
- Planning and archived cards use the same context behavior.
- No production context authority or output contract changes.

## Tests

- Completed-card context source path.
- P046 context determinism and read-only behavior.
- Full regression.

## Stop conditions

- Stop if the fix changes production context output to satisfy the test.
- Stop if either lifecycle path is reintroduced as a fallback.


## Completion

- Completed: 2026-07-26
- Evidence: P046 context acceptance now derives the current card path from the registry and passes after planning-to-archive lifecycle transition; 238 tests passed with 1 external smoke skipped, architecture firewall and compileall passed.
