# M086 — Exact stage-entry proof

- Origin: Modify
- Status: Done
- Depends on: M083, M085

## Goal

Represent one exact, durable predecessor-stage exit as a no-authority
stage-entry proof that downstream materialization and presentation controls can
consume without trusting a caller-authored stage label.

## Boundaries

- This card defines and validates the proof value only.  It does not yet wire
  the proof into every geometry producer, CAD adapter, viewer, or typed loader.
- The proof grants no geometry mutation, persistence, selection, stage
  acceptance, promotion, or canonical-write authority.
- Historical proposal artifacts remain proposal-only; this proof does not
  retroactively promote them.

## Acceptance

- `StageEntryProof@1` binds the deterministic phase gate, exact P036 stage-exit
  checkpoint ref, proof/checkpoint digests, and exact successor branch epoch.
- Construction rejects cross-project checkpoints, non-JSON records, wrong
  record paths, and a successor other than the same run/branch at epoch + 1.
- `require_stage_entry_proof()` rejects stale successor epochs, cross-branch
  successors, and wrong from/to phase claims.
- Exact roundtrip preserves identity and all authority fields remain false.
- The contract has focused tests, compileall, architecture, and diff checks.

## Tests

- Exact roundtrip and guard pass.
- Stale epoch, cross-branch successor, and wrong-phase rejection.
- Architecture check, compileall, and diff check.

## Stop conditions

- Stop before claiming this value alone blocks all low-level geometry or CAD
  calls; those integrations require a separate bounded card.
- Stop before letting a caller-authored boolean or label substitute for the
  exact P036/checkpoint bindings.
- Stop if the proof acquires acceptance, persistence, geometry, or canonical
  authority.


## Completion

- Completed: 2026-08-31
- Evidence: 14 design-maturity tests passed including exact StageEntryProof roundtrip plus stale-epoch, cross-branch, and wrong-phase rejection; compileall, git diff check, tests.test_archcheck, and ARCHITECTURE PASS (193 files) passed. The value remains a no-authority foundation and is not claimed as universal CAD/viewer enforcement.
