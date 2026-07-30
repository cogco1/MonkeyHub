# P025 — Player authority and reversible control interface

- Origin: Planning
- Status: Done
- Depends on: M003, P024, P043

## Goal

Define the player-facing authority boundary for ambiguity confirmation,
program/area inspection, ghost preview, revision, pause/cancel, exact undo,
materials, and final selection without granting a UI action power to waive hard
usability.

## Write scope

- `archflow/interaction/`
- `archflow/runtime/player_control.py`
- `archflow/runtime/README.md`
- `tests/test_player_control.py`
- `tests/integration/test_preview_undo_contract.py`
- `docs/mapping/`

## Acceptance

- Confirmation changes only commitments the player is authorized to change.
- Candidate-stage controls reuse P043 authority identity and receipts without
  conflating early clarification with final preview or approval.
- When policy requires human approval, its receipt binds the exact candidate,
  plan, authority, and validity window; any revision invalidates it.
- A disposable sandbox may remain autonomous only when an explicit prior
  authorization policy permits it.
- Preview exposes bounds, additions/removals, collisions, materials, and
  unresolved obligations before world write.
- Move/rotate/revise creates a new exact-base proposal.
- Pause, cancel, and undo preserve or restore named world/candidate states.
- Aesthetic selection cannot waive hard or commitment failure.

## Tests

- Ambiguity confirmation and unauthorized commitment edit.
- Preview/revise exact-plan invalidation.
- Pause/cancel/undo state preservation.
- Material shortage and protected-object warning receipts.

## Current evidence

- `CandidateProgramInspection@1` exposes only the selected candidate's
  project-derived function, area, material, or other requested facets. It
  cannot recompile a program, choose missing values, write a world, or advance
  canonical state.
- `CandidatePreviewReceipt@1` binds the candidate assembly, submission, frozen
  plan, canonical base, workspace, server, named world and dimension. It
  exposes bounds, additions/removals, collisions, resource availability and
  shortages, protected-object warnings, and unresolved obligations before any
  external write.
- A P043 `AuthorityDecisionReceipt` is reused only as exact project/run/base
  identity evidence. Candidate approval has its own policy-bound receipt and
  event, exact candidate/plan/base/workspace binding, named authority, expiry,
  and explicit denial of hard-gate, commitment-waiver, and canonical-write
  authority.
- Human-required and pre-authorized-disposable policies remain distinct.
  Automation succeeds only when the candidate binds both the exact approval
  policy and an exact `BuildPolicy@1` that explicitly marks the world as a
  disposable sandbox.
- Move, rotate, or general revision accepts a separately project-authored
  candidate assembly on the same exact base and requires a new plan and
  submission identity. The proposal records the prior approval as invalidated
  but performs no edit itself.
- Pause and cancel retain the exact candidate, base, workspace, and named
  world. A written candidate cannot be silently cancelled. Undo reports
  `restored` only when an M003 trace matches the exact plan/base/workspace/
  server/world boundary and proves an acknowledged undo token by hash;
  compensation failure, a bare status label, or any mismatch requires manual
  reconciliation.
- Player option, material, and aesthetic preference requires an authority named
  by the candidate's approval policy. Promotion readiness still requires a
  passing independent hard-validation receipt and completion-boundary
  commitment-monitor receipt; it has no committer.
- P025 targeted tests pass (10 tests). Full regression passes (278 tests, 1
  external smoke skipped), compileall passes, scope check passes, and the
  architecture firewall passes 87 production files.

## Stop conditions

- Stop before implementing a specific game UI framework without a separate
  adapter card.
- Stop if undo cannot prove its restore boundary.


## Completion

- Completed: 2026-07-27
- Evidence: Implemented UI-neutral exact-base program inspection, preview impacts, P043-backed human identity with distinct expiring candidate approval, explicit disposable dual-policy automation, revision invalidation, pause/cancel, exact-token undo reconciliation, policy-authorized preference, and hard-plus-commitment promotion readiness without canonical authority. 10 targeted tests and 278 full tests pass with 1 external smoke skipped; architecture firewall passes 87 files; compileall, scope, and machine verification pass.
