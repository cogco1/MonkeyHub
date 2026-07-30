# M018 — Bounded Minecraft volume observer

- Origin: Modify
- Status: Done
- Depends on: P004

## Goal

Add an optional downstream Minecraft volume-scan capability that returns a
complete or explicitly incomplete `VoxelScan@1` value for project-owned
persistence. It may compare a deployed world with sandbox evidence, but it is
not a prerequisite for P030 or the core sandbox Gold path.

## Write scope

- `archflow/adapters/minecraft_volume.py`
- `archflow/adapters/voxel_observation.py`
- `archflow/adapters/README.md`
- `tests/test_minecraft_volume.py`
- `tests/integration/test_live_use_scenarios.py`
- `probes/p030-live-validation/`
- `governance/work_registry.json`
- `docs/mapping/`

## External provider contract

The selected disposable-world Minecraft driver may be extended with one
read-only `minecraft_scan_volume` tool. Its repository remains the authority
for that provider implementation; ArchFlow owns only the neutral scan contract,
strict response validation, immutable scan values, and validation use.

## Acceptance

- Requests name absolute inclusive bounds and reject oversized volumes.
- The provider reads only already loaded chunks and never loads, places, digs,
  executes, undoes, saves, or commits.
- Every requested coordinate is returned exactly once as known or unknown.
- Collision-bearing cells are solid; collision-free door, trapdoor, and gate
  cells are openings; other collision-free cells are air.
- The adapter rejects wrong bounds, missing cells, duplicate cells, invalid
  kinds, or a workspace/base mismatch, and owns no filesystem writer.
- A valid response becomes optional deterministic `VoxelScan@1` comparison
  input for P004 and the P030 route evaluator without satisfying P030's primary
  sandbox-observation binding.
- The loopback protocol sandbox passes before any disposable-world rerun.

## Tests

- Complete room-shaped volume conversion.
- Loopback HTTP room comparison through P004 and the P030 evaluator, with no
  hard-validation authority.
- Explicit unloaded-chunk unknown preservation.
- Wrong bounds, duplicate, missing, oversized, and wrong-base Reds.
- Minecraft provider compilation.
- Explicit disposable-world observer smoke independent of sandbox acceptance.

## Stop conditions

- Stop if completeness requires loading an unloaded chunk.
- Stop if the observer requires any Minecraft mutation authority.
- Stop after two failures of the same provider or extraction path.

## Implemented authority boundary

- Loopback and opt-in live observations may demonstrate that the shared route
  evaluator can measure an external deployment.
- The external observation is not passed to `UseScenarioValidator` as primary
  evidence. Only P048's exact sandbox-realization receipt may satisfy that
  binding.


## Completion

- Completed: 2026-07-29
- Evidence: Implemented a bounded read-only complete-or-explicitly-unknown volume observer and aligned its authority with the sandbox-first roadmap: loopback and opt-in external observations can run the deterministic route evaluator as comparison evidence, but cannot instantiate P030 primary sandbox validation; 20 combined focused tests passed with 1 opt-in skip, M018 machine verification passed, and no Minecraft mutation or accepted-building authority was added.
