# M004 — Approval and commit wiring

- Origin: Modify
- Status: Done
- Depends on: P015, P016, P024, P025

## Goal

Wire policy-bound human approval or prior disposable-sandbox authorization into
the existing decision package and single-writer commit boundary.

## Write scope

- `archflow/submission/`
- `archflow/commit/`
- `archflow/runtime/player_control.py`
- `tests/test_submission.py`
- `tests/test_promotion.py`
- `tests/test_player_control.py`
- `docs/ARCHITECTURE.md`
- `docs/diagrams/v4-bounded-agency.svg`
- `docs/diagrams/v4-bounded-agency.png`
- `docs/mapping/`

## Acceptance

- `ApprovalPolicy` declares whether an exact candidate requires human approval
  or already has bounded disposable-sandbox authorization.
- A required `ApprovalReceipt` binds candidate, plan, authority, base version,
  and validity window.
- Revision, plan change, stale base, expiry, or authority mismatch invalidates
  approval.
- Approval cannot waive hard usability or commitment failure.
- Missing required approval prevents canonical commit.

## Tests

- Required approval pass and missing receipt.
- Pre-authorized disposable automation.
- Revision, expiry, and stale approval invalidation.
- Approval cannot waive a hard gate.

## Stop conditions

- Stop if all runs are forced to pause for approval regardless of policy.
- Stop if UI confirmation gains canonical write authority.

## Implemented boundary

- The source submission keeps the Architect's pre-execution candidate identity;
  the reviewed submission separately binds the exact executed artifact.
- The decision package is read-only and binds both identities, the exact plan
  and base, approval, hard validation, commitment monitoring, and optional
  aesthetic observations without granting any of them waiver authority.
- Only `Committer.commit_decision_package` can promote the package, and it
  rechecks approval validity and the canonical base before the existing
  compare-and-swap advances one version.


## Completion

- Completed: 2026-07-28
- Evidence: Exact source/executed submission identities, policy-bound approval, hard and commitment gates, and read-only decision package are wired into the sole production Committer boundary.
- Evidence: 13 focused submission/promotion tests and 286 full tests passed with 1 external smoke skipped; architecture firewall passed 87 files; compileall and six-path scope check passed.
