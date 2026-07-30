# M007 — Project state identity and authority seam

- Origin: Modify
- Status: Ready after P035
- Depends on: P015, P019, P035

## Goal

Repair the existing in-memory contracts before durable persistence: separate
project canonical identity from run and branch identity, remove duplicate hard
constraint authority, and prevent the legacy `BuildingProgram@1` from becoming
the canonical design-program kernel.

## Write scope

- `archflow/state/`
- `archflow/project/`
- `archflow/commit/`
- `archflow/validation/engine.py`
- `archflow/evaluation/engine.py`
- `archflow/capabilities/experts.py`
- `archflow/runtime/walking_skeleton.py`
- `archflow/runtime/fake_architect.py`
- `archflow/adapters/fake_voxel.py`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- Canonical `C_v` uses project-version identity; run and branch identities are
  separate exact-base references.
- Only authorized typed commitments may provide production hard-constraint
  authority.
- `GoalContract.must` and walking-skeleton defaults are explicitly
  compatibility/test-only and cannot enter the production initializer.
- The building-scoped design program has a canonical/ref boundary; legacy
  `BuildingProgram@1` is only a derived validator compatibility view.
- No project output is written while migrating these contracts.

## Tests

- Project/run/branch stale-base matrix.
- Typed-commitment-only hard authority.
- No production walking-skeleton default.
- Legacy program projection has no reverse authority.
- Existing boundary regression suite.

## Stop conditions

- Stop if migration requires accepting both old and new hard authorities.
- Stop if legacy fixture data must be copied into the production initializer.

## Implementation evidence

- Canonical state now uses `ProjectVersionRef`; `RunRef` and `BranchRef` remain
  separate exact-base objects.
- `initialize_canonical_project` creates no fixture goal or legacy program.
- `AuthorizedCommitmentClaimsValidator` reads only authorized hard typed
  commitments; the old goal-claims gate is compatibility-only.
- The canonical design program is a `ProjectRecordRef`. `BuildingProgram@1`
  remains only as `legacy_program_view` and is not copied into expert input.
- 90 tests pass, including the identity, authority, initializer, and
  no-reverse-authority matrix. No probe or project output was written.


## Completion

- Completed: 2026-07-25
- Evidence: Canonical project-version identity separated from run/branch; production state has no GoalContract or legacy program default; only authorized typed commitments gate claims; legacy program no longer auto-enters expert snapshots; 90 tests and compileall pass; no project output written.
