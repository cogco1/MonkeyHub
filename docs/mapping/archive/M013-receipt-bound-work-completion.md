# M013 — Receipt-bound work completion

- Origin: Modify
- Status: Done
- Depends on: P001, M006, P036

## Goal

Repair the existing development-control boundary so a work card cannot complete
from a free-text evidence claim alone. Verification must execute bounded
commands and bind the successful result to the current work contract and source
state.

## Write scope

- `tools/devctl.py`
- `tests/test_devctl.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Acceptance

- `devctl verify <ID>` executes the card's structured commands.
- A successful receipt records command identities and output digests.
- The receipt binds the work contract and current declared-scope digest.
- A changed contract or source file invalidates the receipt.
- `devctl complete` rejects missing, malformed, failed, or stale receipts.
- Cards without structured commands use an explicit full-test fallback.
- Verification remains governance evidence, not project or canonical state.

## Tests

- Structured and fallback command normalization.
- Stable contract and scope digests.
- Changed source invalidates a receipt.
- Missing, malformed, and stale receipt rejection.
- Existing status, next, scope, and rendering behavior remains valid.

## Stop conditions

- Stop if verification creates another project persistence authority.
- Stop if a receipt can remain valid after relevant source changes.
- Stop if arbitrary free-text evidence can bypass executable verification.
- Stop if verification requires ingesting project probes or unrelated history.


## Completion

- Completed: 2026-07-26
- Evidence: devctl verify executed 2 declared commands; 8 focused tests and compileall passed, with contract and scope digests recorded in ArchFlowWorkVerification@1.
- Evidence: Completion now rejects missing, malformed, failed, actor-mismatched, contract-stale, and source-stale receipts before any card move or registry transition.
